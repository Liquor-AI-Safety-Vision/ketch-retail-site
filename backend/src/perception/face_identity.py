"""Face detection, alignment, embedding and gallery matching.

Production shape, local storage: detect a face inside one person's crop,
align it to ArcFace's 112x112 template, embed it with an ArcFace ONNX model,
then match against a gallery stored in SQLite.

The matcher follows doc 02 section 9: a name is returned only when both

    top1 >= threshold  and  (top1 - top2) >= margin

hold. If either fails, the result is "unknown" rather than a guess.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import cv2
import numpy as np
import onnxruntime as ort

from backend.src.perception.face_align import FaceDetector, align
from backend.src.common.paths import schema_path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def preferred_providers(prefer_coreml=True):
    avail = ort.get_available_providers()
    out = []
    if prefer_coreml and "CoreMLExecutionProvider" in avail:
        out.append("CoreMLExecutionProvider")
    if sys.platform != "darwin" and "CUDAExecutionProvider" in avail:
        out.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in avail:
        out.append("CPUExecutionProvider")
    return out or ["CPUExecutionProvider"]


def _session(path, providers=None, prefer_coreml=True):
    so = ort.SessionOptions()
    so.log_severity_level = 3
    return ort.InferenceSession(
        path, so,
        providers=providers or preferred_providers(prefer_coreml=prefer_coreml))


def _clip_box(box, W, H):
    x1, y1, x2, y2 = map(float, box)
    x1 = max(0.0, min(x1, W - 1.0))
    y1 = max(0.0, min(y1, H - 1.0))
    x2 = max(0.0, min(x2, W - 1.0))
    y2 = max(0.0, min(y2, H - 1.0))
    return np.array([x1, y1, x2, y2], np.float32)


def expand_box(box, frac, W, H):
    x1, y1, x2, y2 = map(float, box)
    w = max(1.0, x2 - x1)
    h = max(1.0, y2 - y1)
    pad_x, pad_y = frac * w, frac * h
    return _clip_box([x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y], W, H)


@dataclass
class FaceObservation:
    chip_bgr: np.ndarray
    embedding: np.ndarray
    detector_score: float
    face_box_xyxy: np.ndarray
    landmarks_xy: np.ndarray
    person_box_xyxy: np.ndarray
    crop_box_xyxy: np.ndarray


@dataclass
class PersonMatch:
    person_id: str
    display_name: str
    is_staff: bool
    company_id: Optional[str]
    store_id: Optional[str]
    similarity: float
    embedding_id: int
    image_path: Optional[str]


@dataclass
class MatchDecision:
    observation: Optional[FaceObservation]
    person: Optional[PersonMatch]
    top1: Optional[PersonMatch]
    top2: Optional[PersonMatch]
    threshold: float
    margin: float
    reason: str

    @property
    def identity(self):
        return None if self.person is None else self.person.person_id

    @property
    def display_name(self):
        return "Unknown" if self.person is None else self.person.display_name

    @property
    def is_unknown(self):
        return self.person is None


class ArcFaceEmbedder:
    def __init__(self, path, providers=None, prefer_coreml=True):
        self.sess = _session(path, providers=providers, prefer_coreml=prefer_coreml)
        self.inp = self.sess.get_inputs()[0].name
        shape = self.sess.get_inputs()[0].shape
        if len(shape) != 4 or shape[1:] != [3, 112, 112]:
            raise ValueError(f"unexpected ArcFace input shape: {shape}")

    def __call__(self, chip_bgr):
        if chip_bgr.shape[:2] != (112, 112):
            chip_bgr = cv2.resize(chip_bgr, (112, 112))
        x = cv2.cvtColor(chip_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        x = (x - 127.5) / 127.5
        x = np.transpose(x, (2, 0, 1))[None]
        feat = self.sess.run(None, {self.inp: np.ascontiguousarray(x)})[0][0]
        feat = feat.astype(np.float32)
        return feat / (np.linalg.norm(feat) + 1e-9)


class PersonCropFaceExtractor:
    """Face detector scoped to one person crop, not the whole frame."""

    def __init__(self, face_model_path, face_conf=0.20, crop_expand=0.12,
                 min_face_score=0.20, iou=0.45, providers=None, prefer_coreml=True):
        self.face_conf = face_conf
        self.crop_expand = crop_expand
        self.min_face_score = min_face_score
        self.detector = FaceDetector(
            face_model_path, conf=face_conf, iou=iou,
            providers=providers, prefer_coreml=prefer_coreml)

    def detect(self, frame_bgr, person_box_xyxy):
        H, W = frame_bgr.shape[:2]
        crop_box = expand_box(person_box_xyxy, self.crop_expand, W, H)
        x1, y1, x2, y2 = crop_box.astype(int)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame_bgr[y1:y2, x1:x2]
        faces = self.detector(crop)
        if not faces:
            return None

        best = None
        best_key = None
        for box, score, lm in faces:
            if score < self.min_face_score:
                continue
            area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
            # Highest confidence first, then larger face; within a person crop
            # either is reasonable and this ordering is stable.
            key = (float(score), float(area))
            if best is None or key > best_key:
                best = (box.copy(), float(score), lm.copy())
                best_key = key
        if best is None:
            return None

        box, score, lm = best
        box += np.array([x1, y1, x1, y1], np.float32)
        lm += np.array([x1, y1], np.float32)
        chip = align(frame_bgr, lm, size=112)
        if chip is None:
            return None
        return chip, score, box.astype(np.float32), lm.astype(np.float32), crop_box


class FaceGallery:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self):
        self.conn.close()

    def _init_schema(self):
        # Sourced from the same schema.sql SqliteAlertStore applies, not a
        # hand-copied second definition -- persons/face_embeddings used to
        # live in a database of their own (gallery_db_path, separate from
        # db_path) and this file's own inline CREATE TABLE was the only
        # definition of them anywhere. Two independent copies of the same
        # tables is how they drift; one file read from both places is not.
        self.conn.executescript(schema_path("local_dev_schema.sql").read_text())
        # Defensive only: a database built before store_id/company_id were
        # columns in schema.sql (an older gallery_db_path file, say) still
        # opens correctly instead of failing on the first query that
        # references them.
        self._ensure_column("persons", "company_id", "TEXT")
        self._ensure_column("persons", "store_id", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table, column, ddl):
        cols = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column in cols:
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        self.conn.commit()

    def upsert_person(self, person_id, display_name, is_staff=True,
                      company_id=None, store_id=None, metadata=None):
        now = utc_now()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True)
        self.conn.execute(
            """
            INSERT INTO persons
            (person_id, display_name, is_staff, company_id, store_id, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                display_name=excluded.display_name,
                is_staff=excluded.is_staff,
                company_id=excluded.company_id,
                store_id=excluded.store_id,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                person_id, display_name, int(is_staff), company_id, store_id,
                metadata_json, now, now,
            ))
        self.conn.commit()

    def add_embedding(self, person_id, embedding, detector_score=None, image_path=None,
                      source=None, metadata=None):
        emb = np.asarray(embedding, np.float32).reshape(-1)
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        cur = self.conn.execute(
            """
            INSERT INTO face_embeddings
            (person_id, embedding, detector_score, image_path, source, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (person_id, emb.tobytes(), detector_score, image_path, source,
             json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True),
             utc_now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def iter_embeddings(self, store_id=None, company_id=None, is_staff=None):
        """is_staff narrows the gallery to one role: True for staff, False for
        everyone else (a watchlist). None searches both, as it always did."""
        query = """
            SELECT e.embedding_id, e.person_id, e.embedding, e.image_path,
                   p.display_name, p.is_staff, p.company_id, p.store_id
            FROM face_embeddings e
            JOIN persons p USING(person_id)
        """
        where = []
        params = []
        if store_id is not None:
            where.append("p.store_id = ?")
            params.append(store_id)
        if company_id is not None:
            where.append("p.company_id = ?")
            params.append(company_id)
        if is_staff is not None:
            where.append("p.is_staff = ?")
            params.append(int(bool(is_staff)))
        if where:
            query += " WHERE " + " AND ".join(where)
        cur = self.conn.execute(query, params)
        for row in cur:
            emb = np.frombuffer(row["embedding"], np.float32)
            yield {
                "embedding_id": int(row["embedding_id"]),
                "person_id": row["person_id"],
                "display_name": row["display_name"],
                "is_staff": bool(row["is_staff"]),
                "company_id": row["company_id"],
                "store_id": row["store_id"],
                "image_path": row["image_path"],
                "embedding": emb,
            }

    def summarize(self, store_id=None, company_id=None, is_staff=None):
        """Count what match() would actually search under the same filters.

        A scope that matches nothing is indistinguishable from a genuine
        non-match at call time: both surface as "gallery_empty" on every
        frame. Callers use this at startup to say so once, loudly.
        """
        query = """
            SELECT COUNT(DISTINCT p.person_id) AS persons,
                   COUNT(e.embedding_id) AS embeddings
            FROM face_embeddings e
            JOIN persons p USING(person_id)
        """
        where = []
        params = []
        if store_id is not None:
            where.append("p.store_id = ?")
            params.append(store_id)
        if company_id is not None:
            where.append("p.company_id = ?")
            params.append(company_id)
        if is_staff is not None:
            where.append("p.is_staff = ?")
            params.append(int(bool(is_staff)))
        if where:
            query += " WHERE " + " AND ".join(where)
        row = self.conn.execute(query, params).fetchone()
        known = [r["store_id"] for r in self.conn.execute(
            "SELECT DISTINCT store_id FROM persons WHERE store_id IS NOT NULL")]
        return {
            "persons": int(row["persons"]),
            "embeddings": int(row["embeddings"]),
            "known_store_ids": known,
        }

    def match(self, embedding, threshold=0.30, margin=0.10,
              store_id=None, company_id=None, is_staff=None):
        emb = np.asarray(embedding, np.float32).reshape(-1)
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        best_by_person = {}
        for rec in self.iter_embeddings(store_id=store_id, company_id=company_id,
                                        is_staff=is_staff):
            sim = float(np.dot(emb, rec["embedding"]))
            hit = PersonMatch(
                person_id=rec["person_id"],
                display_name=rec["display_name"],
                is_staff=rec["is_staff"],
                company_id=rec["company_id"],
                store_id=rec["store_id"],
                similarity=sim,
                embedding_id=rec["embedding_id"],
                image_path=rec["image_path"],
            )
            prev = best_by_person.get(hit.person_id)
            if prev is None or hit.similarity > prev.similarity:
                best_by_person[hit.person_id] = hit

        ranked = sorted(best_by_person.values(),
                        key=lambda h: h.similarity, reverse=True)
        top1 = ranked[0] if ranked else None
        top2 = ranked[1] if len(ranked) > 1 else None
        if top1 is None:
            return None, top1, top2, "gallery_empty"
        if top1.similarity < threshold:
            return None, top1, top2, "top1_below_threshold"
        gap = top1.similarity - (top2.similarity if top2 else 0.0)
        if top2 is not None and gap < margin:
            return None, top1, top2, "top1_top2_margin_too_small"
        return top1, top1, top2, "matched"


class FaceIdentifier:
    def __init__(self, face_model_path, embed_model_path, gallery_db_path,
                 face_conf=0.20, crop_expand=0.12, iou=0.45, match_threshold=0.30,
                 match_margin=0.10, providers=None, prefer_coreml=True):
        # iou reaches the face detector's NMS. It used to be unreachable --
        # hardcoded at 0.45 inside PersonCropFaceExtractor -- while
        # SharedFaceIdentifier took it as an argument, so face_identity.iou_thres
        # in a manifest applied under one construction path and not the other.
        self.extractor = PersonCropFaceExtractor(
            face_model_path, face_conf=face_conf, crop_expand=crop_expand,
            min_face_score=face_conf, iou=iou, providers=providers,
            prefer_coreml=prefer_coreml)
        self.embedder = ArcFaceEmbedder(
            embed_model_path, providers=providers, prefer_coreml=prefer_coreml)
        self.gallery = FaceGallery(gallery_db_path)
        self.match_threshold = match_threshold
        self.match_margin = match_margin

    def observe(self, frame_bgr, person_box_xyxy):
        rec = self.extractor.detect(frame_bgr, person_box_xyxy)
        if rec is None:
            return None
        chip, det_score, face_box, landmarks, crop_box = rec
        emb = self.embedder(chip)
        return FaceObservation(
            chip_bgr=chip, embedding=emb, detector_score=det_score,
            face_box_xyxy=face_box, landmarks_xy=landmarks,
            person_box_xyxy=np.asarray(person_box_xyxy, np.float32),
            crop_box_xyxy=crop_box.astype(np.float32))

    def identify(self, frame_bgr, person_box_xyxy, store_id=None, company_id=None):
        obs = self.observe(frame_bgr, person_box_xyxy)
        if obs is None:
            return MatchDecision(
                observation=None, person=None, top1=None, top2=None,
                threshold=self.match_threshold, margin=self.match_margin,
                reason="no_face_detected")
        person, top1, top2, reason = self.gallery.match(
            obs.embedding, threshold=self.match_threshold,
            margin=self.match_margin, store_id=store_id,
            company_id=company_id)
        return MatchDecision(
            observation=obs, person=person, top1=top1, top2=top2,
            threshold=self.match_threshold, margin=self.match_margin,
            reason=reason)

    def register(self, person_id, display_name, chip_bgr, detector_score=None,
                 image_path=None, source=None, is_staff=True, metadata=None,
                 company_id=None, store_id=None):
        self.gallery.upsert_person(
            person_id, display_name, is_staff=is_staff,
            company_id=company_id, store_id=store_id, metadata=metadata)
        emb = self.embedder(chip_bgr)
        return self.gallery.add_embedding(
            person_id, emb, detector_score=detector_score,
            image_path=image_path, source=source, metadata=metadata)

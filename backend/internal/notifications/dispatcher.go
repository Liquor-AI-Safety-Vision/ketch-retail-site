package notifications

// AlertDispatcher queues notifications for alerts the AI service has written.
//
// There used to be an ingestion endpoint that queued deliveries in the same
// transaction as the alert. The AI service now writes alerts and media
// straight into the shared database (ai/src/infra/cloud_alert_sink.py), so
// this polls instead: every alert newer than MaxAlertAge that has no row in
// notification_alert_scans is claimed (the INSERT is the claim, so several
// API instances never double-notify), matched against the store's rules, and
// queued for the delivery Worker exactly as the endpoint used to.
//
// The AI service writes the alert row first and its clip about post_s seconds
// later, after the clip window closes and the upload to R2 is verified. A
// notification without the clip is much less useful, so an alert waits up to
// ClipWait for it before being queued without one.

import (
	"context"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type DispatcherOptions struct {
	PollInterval time.Duration
	BatchSize    int
	// Older alerts are never notified -- in particular everything already in
	// the database the first time the dispatcher runs.
	MaxAlertAge time.Duration
	// How long an alert without a clip is held back waiting for one.
	ClipWait time.Duration
}

func (o DispatcherOptions) withDefaults() DispatcherOptions {
	if o.PollInterval <= 0 {
		o.PollInterval = 2 * time.Second
	}
	if o.BatchSize <= 0 {
		o.BatchSize = 20
	}
	if o.MaxAlertAge <= 0 {
		o.MaxAlertAge = 15 * time.Minute
	}
	if o.ClipWait <= 0 {
		o.ClipWait = 90 * time.Second
	}
	return o
}

type AlertDispatcher struct {
	db      *pgxpool.Pool
	log     *slog.Logger
	options DispatcherOptions
	now     func() time.Time
}

func NewAlertDispatcher(db *pgxpool.Pool, logger *slog.Logger, options DispatcherOptions) *AlertDispatcher {
	return &AlertDispatcher{db: db, log: logger, options: options.withDefaults(), now: time.Now}
}

func (d *AlertDispatcher) Run(ctx context.Context) {
	ticker := time.NewTicker(d.options.PollInterval)
	defer ticker.Stop()
	for {
		if _, err := d.RunOnce(ctx); err != nil && ctx.Err() == nil {
			d.log.Error("alert dispatch failed", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

type dispatchCandidate struct {
	input    AlertNotificationInput
	openedAt string
}

// isoUTC matches the AI service's opened_at text (datetime.isoformat() in
// UTC), so the age bounds below compare as text against the column's index.
const isoUTC = "2006-01-02T15:04:05.000000+00:00"

// RunOnce claims and queues one batch. It returns how many alerts it claimed.
func (d *AlertDispatcher) RunOnce(ctx context.Context) (int, error) {
	now := d.now().UTC()
	oldest := now.Add(-d.options.MaxAlertAge).Format(isoUTC)
	clipDeadline := now.Add(-d.options.ClipWait).Format(isoUTC)

	tx, err := d.db.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	rows, err := tx.Query(ctx, `
SELECT a.alert_id, a.store_id, s.display_name, s.timezone, a.event_type, a.severity, a.opened_at,
       a.camera_id, COALESCE(c.display_name, a.camera_id), COALESCE(a.identity_label, ''),
       COALESCE((SELECT m.media_id FROM alert_media m WHERE m.alert_id=a.alert_id AND m.kind='clip'
                 ORDER BY m.started_at NULLS LAST, m.media_id LIMIT 1), '')
FROM alerts a
JOIN stores s ON s.store_id=a.store_id
LEFT JOIN cameras c ON c.camera_id=a.camera_id
WHERE a.opened_at > $1
  AND a.status <> 'dismissed'
  AND NOT EXISTS (SELECT 1 FROM "notification_alert_scans" x WHERE x."alertId"=a.alert_id)
  AND (a.opened_at <= $2 OR EXISTS (SELECT 1 FROM alert_media m WHERE m.alert_id=a.alert_id AND m.kind='clip'))
ORDER BY a.opened_at, a.alert_id
LIMIT $3`, oldest, clipDeadline, d.options.BatchSize)
	if err != nil {
		return 0, err
	}
	candidates := []dispatchCandidate{}
	for rows.Next() {
		var c dispatchCandidate
		in := &c.input
		if err := rows.Scan(&in.AlertID, &in.StoreID, &in.StoreName, &in.StoreTimezone, &in.AlertType, &in.Severity, &c.openedAt,
			&in.CameraID, &in.CameraName, &in.IdentityLabel, &in.MediaID); err != nil {
			rows.Close()
			return 0, err
		}
		candidates = append(candidates, c)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}

	claimed := 0
	for _, c := range candidates {
		var won bool
		var mediaID any
		if c.input.MediaID != "" {
			mediaID = c.input.MediaID
		}
		err := tx.QueryRow(ctx, `INSERT INTO "notification_alert_scans" ("alertId","storeId","mediaId") VALUES ($1,$2,$3) ON CONFLICT DO NOTHING RETURNING true`,
			c.input.AlertID, c.input.StoreID, mediaID).Scan(&won)
		if err == pgx.ErrNoRows {
			continue // another instance claimed it first
		}
		if err != nil {
			return claimed, err
		}
		detectedAt, parseErr := time.Parse(time.RFC3339Nano, c.openedAt)
		if parseErr != nil {
			detectedAt = now
		}
		c.input.DetectedAt = detectedAt
		summaries, err := EnqueueAlertTx(ctx, tx, c.input)
		if err != nil {
			return claimed, err
		}
		if len(summaries) > 0 {
			if _, err := tx.Exec(ctx, `UPDATE "notification_alert_scans" SET "deliveriesQueued"=$2 WHERE "alertId"=$1`, c.input.AlertID, len(summaries)); err != nil {
				return claimed, err
			}
			d.log.Info("alert notifications queued", "alertId", c.input.AlertID, "eventType", c.input.AlertType, "deliveries", len(summaries), "withClip", c.input.MediaID != "")
		}
		claimed++
	}
	if err := tx.Commit(ctx); err != nil {
		return 0, err
	}
	return claimed, nil
}

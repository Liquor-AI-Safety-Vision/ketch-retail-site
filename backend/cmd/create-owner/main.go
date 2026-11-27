// create-owner creates (or resets) a dashboard owner and links it to stores.
//
// Production keeps REGISTER_ENABLED=false, so this is how accounts are made.
// Stores must already exist: the AI service creates them from its manifest.
// The password is read from OWNER_PASSWORD, never from a flag, so it does
// not land in shell history or the process list.
//
//	OWNER_PASSWORD=... go run ./cmd/create-owner -email owner@example.com \
//	    -name "Store owner" -stores store_test_01 [-role owner] [-replace owner_abc]
//
// -replace deletes that owner in the same transaction (its store links and
// sessions go with it), so a store is never left without an owner.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"net/mail"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/liquor-store/security-api/internal/config"
	"github.com/liquor-store/security-api/internal/security"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "create-owner:", err)
		os.Exit(1)
	}
}

func run() error {
	email := flag.String("email", "", "login email")
	name := flag.String("name", "", "display name")
	stores := flag.String("stores", "", "comma-separated store_ids to own")
	role := flag.String("role", "owner", "owner | manager | operator")
	replace := flag.String("replace", "", "owner_id to delete in the same transaction")
	flag.Parse()

	address, err := mail.ParseAddress(*email)
	if err != nil || !strings.EqualFold(address.Address, *email) {
		return fmt.Errorf("-email %q is not an email address", *email)
	}
	normalized := strings.ToLower(*email)
	if strings.TrimSpace(*name) == "" {
		return errors.New("-name is required")
	}
	if *role != "owner" && *role != "manager" && *role != "operator" {
		return fmt.Errorf("-role must be owner, manager or operator")
	}
	storeIDs := []string{}
	for _, id := range strings.Split(*stores, ",") {
		if id = strings.TrimSpace(id); id != "" {
			storeIDs = append(storeIDs, id)
		}
	}
	if len(storeIDs) == 0 {
		return errors.New("-stores is required")
	}
	password := os.Getenv("OWNER_PASSWORD")
	if password == "" || len(password) > 128 {
		return errors.New("OWNER_PASSWORD must be set (1-128 characters)")
	}
	hash, err := security.HashPassword(password)
	if err != nil {
		return err
	}

	cfg, err := config.Load()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, cfg.MigrationURL())
	if err != nil {
		return err
	}
	defer conn.Close(context.Background())
	tx, err := conn.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	for _, id := range storeIDs {
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM stores WHERE store_id=$1)`, id).Scan(&exists); err != nil {
			return err
		}
		if !exists {
			return fmt.Errorf("store %q does not exist (the AI service creates stores from its manifest)", id)
		}
	}
	if *replace != "" {
		tag, err := tx.Exec(ctx, `DELETE FROM owners WHERE owner_id=$1`, *replace)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return fmt.Errorf("-replace: owner %q not found", *replace)
		}
	}

	now := time.Now().UTC().Format("2006-01-02T15:04:05.000000+00:00")
	var ownerID string
	err = tx.QueryRow(ctx, `SELECT owner_id FROM owners WHERE lower(email)=$1`, normalized).Scan(&ownerID)
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		suffix := make([]byte, 6)
		if _, err := rand.Read(suffix); err != nil {
			return err
		}
		ownerID = "owner_" + hex.EncodeToString(suffix)
		if _, err := tx.Exec(ctx, `INSERT INTO owners (owner_id, email, display_name, password_hash, status, metadata_json, created_at, updated_at)
VALUES ($1,$2,$3,$4,'active','{}',$5,$5)`, ownerID, normalized, *name, hash, now); err != nil {
			return err
		}
	case err != nil:
		return err
	default:
		if _, err := tx.Exec(ctx, `UPDATE owners SET display_name=$2, password_hash=$3, status='active', updated_at=$4 WHERE owner_id=$1`,
			ownerID, *name, hash, now); err != nil {
			return err
		}
		// A reset password ends every session signed in with the old one.
		if _, err := tx.Exec(ctx, `DELETE FROM refresh_sessions WHERE "ownerId"=$1`, ownerID); err != nil {
			return err
		}
	}
	for _, id := range storeIDs {
		if _, err := tx.Exec(ctx, `INSERT INTO owner_stores (owner_id, store_id, role, created_at) VALUES ($1,$2,$3,$4)
ON CONFLICT (owner_id, store_id) DO UPDATE SET role=EXCLUDED.role`, ownerID, id, *role, now); err != nil {
			return err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return err
	}
	fmt.Printf("owner %s (%s) is %s of %s\n", ownerID, normalized, *role, strings.Join(storeIDs, ", "))
	return nil
}

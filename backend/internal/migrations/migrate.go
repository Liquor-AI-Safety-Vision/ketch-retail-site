// Package migrations brings a database to the schema this API reads and writes.
//
// Two layers, applied in this order on every run:
//
//  1. ai_schema.sql -- the AI service's schema (stores, cameras, owners,
//     owner_stores, alerts, alert_media, persons, face_embeddings). It is a
//     byte-for-byte copy of ai/sql/postgres_schema.sql, which is the source of
//     truth: the AI service applies the same file every time it starts, and
//     every statement in it is idempotent (IF NOT EXISTS). TestEmbeddedAISchema
//     fails if the copy drifts; refresh it with `go generate ./internal/migrations`.
//     Applying it here as well means the API can start against an empty
//     database, and never against an older layout than the one it was built for.
//
//  2. sql/*.sql -- tables only this API owns (sessions, review history,
//     notifications), applied once each and recorded in go_schema_migrations.
package migrations

//go:generate cp ../../../ai/sql/postgres_schema.sql ai_schema.sql

import (
	"context"
	"embed"
	"fmt"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
)

//go:embed ai_schema.sql
var aiSchema string

//go:embed sql/*.sql
var migrationFiles embed.FS

// AISchema is the embedded AI service schema, for tests that compare it.
func AISchema() string { return aiSchema }

func Apply(ctx context.Context, conn *pgx.Conn) (string, error) {
	if _, err := conn.Exec(ctx, aiSchema); err != nil {
		return "", fmt.Errorf("apply AI schema (ai/sql/postgres_schema.sql): %w", err)
	}
	if _, err := conn.Exec(ctx, `CREATE TABLE IF NOT EXISTS "go_schema_migrations" ("version" TEXT PRIMARY KEY, "appliedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW())`); err != nil {
		return "", fmt.Errorf("create migration table: %w", err)
	}

	entries, err := migrationFiles.ReadDir("sql")
	if err != nil {
		return "", fmt.Errorf("read embedded migrations: %w", err)
	}
	filenames := make([]string, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".sql") {
			filenames = append(filenames, entry.Name())
		}
	}
	sort.Strings(filenames)

	appliedCount := 0
	for _, filename := range filenames {
		version := strings.TrimSuffix(filename, ".sql")
		var applied bool
		if err := conn.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM "go_schema_migrations" WHERE "version"=$1)`, version).Scan(&applied); err != nil {
			return "", fmt.Errorf("check migration %s: %w", version, err)
		}
		if applied {
			continue
		}

		migrationSQL, err := migrationFiles.ReadFile("sql/" + filename)
		if err != nil {
			return "", fmt.Errorf("read migration %s: %w", version, err)
		}

		tx, err := conn.Begin(ctx)
		if err != nil {
			return "", fmt.Errorf("begin migration %s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, string(migrationSQL)); err != nil {
			_ = tx.Rollback(ctx)
			return "", fmt.Errorf("apply migration %s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, `INSERT INTO "go_schema_migrations" ("version") VALUES ($1)`, version); err != nil {
			_ = tx.Rollback(ctx)
			return "", fmt.Errorf("record migration %s: %w", version, err)
		}
		if err := tx.Commit(ctx); err != nil {
			return "", fmt.Errorf("commit migration %s: %w", version, err)
		}
		appliedCount++
	}

	if appliedCount == 0 {
		return "Database is already up to date.", nil
	}
	return fmt.Sprintf("Applied %d database migration(s).", appliedCount), nil
}

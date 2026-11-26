package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/liquor-store/security-api/internal/config"
	"github.com/liquor-store/security-api/internal/notifications"
)

// Configures one store's notification routing: Telegram primary, WhatsApp
// fallback when its variables are set, and one rule covering the AI
// service's alerts.
//
//	APPLY_PRODUCTION_NOTIFICATION_CONFIG=1   required, this writes routing
//	STORE_ID                                 the AI manifest's store_id
//	TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID     required
//	NOTIFICATION_RULE_MIN_SEVERITY           warning | high | critical (default warning:
//	                                         every AI alert; storage and entrance
//	                                         alerts are warning, counter alerts high)
//	NOTIFICATION_RULE_ALERT_TYPES            comma-separated event_type values
//	                                         (default empty = all types)
//	WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_WABA_ID,
//	WHATSAPP_RECIPIENT_PHONE, WHATSAPP_OPT_IN_CAPTURED_AT
//	                                         optional; all or none
const productionRuleName = "AI alerts"

var aiEventTypes = map[string]bool{
	"suspected_cash_concealment": true, "unknown_entry": true, "staff_outside_schedule": true,
	"watchlist_match": true, "weapon_detected": true,
}

func main() {
	if os.Getenv("APPLY_PRODUCTION_NOTIFICATION_CONFIG") != "1" {
		fatal(errors.New("refusing to change notification routing without APPLY_PRODUCTION_NOTIFICATION_CONFIG=1"))
	}
	cfg, err := config.Load()
	if err != nil {
		fatal(err)
	}
	storeID := requiredEnv("STORE_ID")
	requiredEnv("TELEGRAM_BOT_TOKEN")
	telegramChatID := requiredEnv("TELEGRAM_CHAT_ID")
	minimumSeverity := strings.ToLower(strings.TrimSpace(os.Getenv("NOTIFICATION_RULE_MIN_SEVERITY")))
	if minimumSeverity == "" {
		minimumSeverity = "warning"
	}
	if !notifications.ValidSeverity(minimumSeverity) {
		fatal(errors.New("NOTIFICATION_RULE_MIN_SEVERITY must be warning, high or critical"))
	}
	alertTypes := []string{}
	for _, value := range strings.Split(os.Getenv("NOTIFICATION_RULE_ALERT_TYPES"), ",") {
		value = strings.ToLower(strings.TrimSpace(value))
		if value == "" {
			continue
		}
		if !aiEventTypes[value] {
			fatal(fmt.Errorf("NOTIFICATION_RULE_ALERT_TYPES: %q is not an AI event type", value))
		}
		alertTypes = append(alertTypes, value)
	}

	whatsAppNames := []string{"WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_WABA_ID", "WHATSAPP_RECIPIENT_PHONE", "WHATSAPP_OPT_IN_CAPTURED_AT"}
	whatsAppSet := 0
	for _, name := range whatsAppNames {
		if strings.TrimSpace(os.Getenv(name)) != "" {
			whatsAppSet++
		}
	}
	if whatsAppSet != 0 && whatsAppSet != len(whatsAppNames) {
		fatal(errors.New("set all WhatsApp variables or none"))
	}
	useWhatsApp := whatsAppSet == len(whatsAppNames)
	var whatsAppConfig json.RawMessage
	var phoneNumberID, recipientPhone string
	if useWhatsApp {
		phoneNumberID, recipientPhone = requiredEnv("WHATSAPP_PHONE_NUMBER_ID"), requiredEnv("WHATSAPP_RECIPIENT_PHONE")
		optInCapturedAt := requiredEnv("WHATSAPP_OPT_IN_CAPTURED_AT")
		if _, err := time.Parse(time.RFC3339, optInCapturedAt); err != nil {
			fatal(errors.New("WHATSAPP_OPT_IN_CAPTURED_AT must be RFC3339"))
		}
		whatsAppConfig, err = json.Marshal(map[string]any{
			"wabaId": requiredEnv("WHATSAPP_WABA_ID"), "templateName": notifications.WhatsAppLinkedTemplateName,
			"templateLanguage": notifications.WhatsAppTemplateLanguage, "templateVersion": notifications.WhatsAppLinkedTemplateVersion,
			"optIn": map[string]any{"capturedAt": optInCapturedAt, "source": "OWNER_PRODUCTION_CONFIGURATION", "policyVersion": "whatsapp-emergency-alerts-v1"},
		})
		if err != nil {
			fatal(err)
		}
		if err := notifications.ValidateWhatsAppEnableConfig(phoneNumberID, recipientPhone, whatsAppConfig); err != nil {
			fatal(err)
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, cfg.MigrationURL())
	if err != nil {
		fatal(err)
	}
	defer conn.Close(context.Background())
	tx, err := conn.Begin(ctx)
	if err != nil {
		fatal(err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var storeExists bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM stores WHERE store_id=$1)`, storeID).Scan(&storeExists); err != nil {
		fatal(err)
	}
	if !storeExists {
		fatal(fmt.Errorf("store %q does not exist; the AI service creates stores from its manifest at startup", storeID))
	}
	telegramConfig := json.RawMessage(`{"parseMode":"HTML"}`)
	telegramID, err := configureEndpoint(ctx, tx, storeID, string(notifications.ProviderTelegram), "Owner Telegram", "", telegramChatID, "env://TELEGRAM_BOT_TOKEN", telegramConfig)
	if err != nil {
		fatal(err)
	}
	ruleID, err := configureRule(ctx, tx, storeID, minimumSeverity, alertTypes)
	if err != nil {
		fatal(err)
	}
	if err := configureChannel(ctx, tx, storeID, ruleID, telegramID, 1, 0); err != nil {
		fatal(err)
	}
	if useWhatsApp {
		whatsAppID, err := configureEndpoint(ctx, tx, storeID, string(notifications.ProviderWhatsApp), "Owner WhatsApp", phoneNumberID, recipientPhone, "env://WHATSAPP_ACCESS_TOKEN", whatsAppConfig)
		if err != nil {
			fatal(err)
		}
		if err := configureChannel(ctx, tx, storeID, ruleID, whatsAppID, 2, 60); err != nil {
			fatal(err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		fatal(err)
	}
	fmt.Printf("Configured rule %q (minimum severity %s, types %v) with Telegram primary, WhatsApp fallback: %v. No credentials or destinations were printed.\n",
		productionRuleName, minimumSeverity, alertTypes, useWhatsApp)
}

func configureEndpoint(ctx context.Context, tx pgx.Tx, storeID, provider, label, providerAccountRef, destinationRef, credentialRef string, endpointConfig json.RawMessage) (string, error) {
	rows, err := tx.Query(ctx, `SELECT "id" FROM "notification_endpoints" WHERE "storeId"=$1 AND "provider"=$2 ORDER BY "createdAt","id" LIMIT 2`, storeID, provider)
	if err != nil {
		return "", err
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return "", err
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return "", err
	}
	rows.Close()
	if len(ids) > 1 {
		return "", fmt.Errorf("multiple %s endpoints exist; refusing to guess which one to update", provider)
	}
	accountRef := any(nil)
	if strings.TrimSpace(providerAccountRef) != "" {
		accountRef = providerAccountRef
	}
	if len(ids) == 1 {
		_, err = tx.Exec(ctx, `UPDATE "notification_endpoints" SET "label"=$3,"providerAccountRef"=$4,"destinationRef"=$5,"credentialRef"=$6,"config"=$7,"isEnabled"=true,"updatedAt"=NOW() WHERE "id"=$1 AND "storeId"=$2`, ids[0], storeID, label, accountRef, destinationRef, credentialRef, endpointConfig)
		return ids[0], err
	}
	id := uuid.NewString()
	_, err = tx.Exec(ctx, `INSERT INTO "notification_endpoints" ("id","storeId","provider","label","providerAccountRef","destinationRef","credentialRef","config","isEnabled","updatedAt") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,true,NOW())`, id, storeID, provider, label, accountRef, destinationRef, credentialRef, endpointConfig)
	return id, err
}

func configureRule(ctx context.Context, tx pgx.Tx, storeID, minimumSeverity string, alertTypes []string) (string, error) {
	var id string
	err := tx.QueryRow(ctx, `SELECT "id" FROM "notification_rules" WHERE "storeId"=$1 AND "name"=$2 ORDER BY "createdAt","id" LIMIT 1`, storeID, productionRuleName).Scan(&id)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return "", err
	}
	if errors.Is(err, pgx.ErrNoRows) {
		id = uuid.NewString()
		_, err = tx.Exec(ctx, `INSERT INTO "notification_rules" ("id","storeId","name","minimumSeverity","alertTypes","cooldownSeconds","isEnabled","updatedAt") VALUES ($1,$2,$3,$4,$5,0,true,NOW())`, id, storeID, productionRuleName, minimumSeverity, alertTypes)
		return id, err
	}
	_, err = tx.Exec(ctx, `UPDATE "notification_rules" SET "minimumSeverity"=$3,"alertTypes"=$4,"cooldownSeconds"=0,"isEnabled"=true,"updatedAt"=NOW() WHERE "id"=$1 AND "storeId"=$2`, id, storeID, minimumSeverity, alertTypes)
	return id, err
}

func configureChannel(ctx context.Context, tx pgx.Tx, storeID, ruleID, endpointID string, priority, fallbackDelay int) error {
	var id string
	err := tx.QueryRow(ctx, `SELECT "id" FROM "notification_rule_channels" WHERE "ruleId"=$1 AND "endpointId"=$2`, ruleID, endpointID).Scan(&id)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	if errors.Is(err, pgx.ErrNoRows) {
		_, err = tx.Exec(ctx, `INSERT INTO "notification_rule_channels" ("id","storeId","ruleId","endpointId","priority","fallbackDelaySeconds","isEnabled","updatedAt") VALUES ($1,$2,$3,$4,$5,$6,true,NOW())`, uuid.NewString(), storeID, ruleID, endpointID, priority, fallbackDelay)
		return err
	}
	_, err = tx.Exec(ctx, `UPDATE "notification_rule_channels" SET "priority"=$3,"fallbackDelaySeconds"=$4,"isEnabled"=true,"updatedAt"=NOW() WHERE "id"=$1 AND "storeId"=$2`, id, storeID, priority, fallbackDelay)
	return err
}

func requiredEnv(name string) string {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		fatal(fmt.Errorf("%s is required", name))
	}
	return value
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}

package controller

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func TestApplyPlaygroundDefaultsToOpenAIRequest(t *testing.T) {
	req := &dto.GeneralOpenAIRequest{}
	topP := 0.9
	req.TopP = &topP

	config := map[string]any{
		"inputs": map[string]any{
			"temperature":       0.2,
			"top_p":             0.5,
			"max_tokens":        2048,
			"frequency_penalty": 0.1,
			"presence_penalty":  0.3,
			"seed":              7,
		},
		"parameterEnabled": map[string]any{
			"temperature":       true,
			"top_p":             true,
			"max_tokens":        true,
			"frequency_penalty": true,
			"presence_penalty":  true,
			"seed":              true,
		},
	}

	applyPlaygroundDefaultsToOpenAIRequest(req, config)

	if req.Temperature == nil || *req.Temperature != 0.2 {
		t.Fatalf("expected temperature to be injected, got %#v", req.Temperature)
	}
	if req.TopP == nil || *req.TopP != 0.9 {
		t.Fatalf("expected explicit top_p to win, got %#v", req.TopP)
	}
	if req.MaxTokens == nil || *req.MaxTokens != 2048 {
		t.Fatalf("expected max_tokens to be injected, got %#v", req.MaxTokens)
	}
	if req.FrequencyPenalty == nil || *req.FrequencyPenalty != 0.1 {
		t.Fatalf("expected frequency_penalty to be injected, got %#v", req.FrequencyPenalty)
	}
	if req.PresencePenalty == nil || *req.PresencePenalty != 0.3 {
		t.Fatalf("expected presence_penalty to be injected, got %#v", req.PresencePenalty)
	}
	if req.Seed == nil || *req.Seed != 7 {
		t.Fatalf("expected seed to be injected, got %#v", req.Seed)
	}
}

func TestApplyPlaygroundDefaultsToResponsesRequest(t *testing.T) {
	req := &dto.OpenAIResponsesRequest{}
	topP := 0.95
	req.TopP = &topP

	config := map[string]any{
		"inputs": map[string]any{
			"temperature":       0.4,
			"top_p":             0.2,
			"max_tokens":        1024,
			"frequency_penalty": 0.5,
		},
		"parameterEnabled": map[string]any{
			"temperature":       true,
			"top_p":             true,
			"max_tokens":        true,
			"frequency_penalty": true,
		},
	}

	applyPlaygroundDefaultsToResponsesRequest(req, config)

	if req.Temperature == nil || *req.Temperature != 0.4 {
		t.Fatalf("expected temperature to be injected, got %#v", req.Temperature)
	}
	if req.TopP == nil || *req.TopP != 0.95 {
		t.Fatalf("expected explicit top_p to win, got %#v", req.TopP)
	}
	if req.MaxOutputTokens == nil || *req.MaxOutputTokens != 1024 {
		t.Fatalf("expected max_output_tokens to be injected, got %#v", req.MaxOutputTokens)
	}
}

func setupPlaygroundDefaultsTestDB(t *testing.T) *gorm.DB {
	t.Helper()

	gin.SetMode(gin.TestMode)
	common.UsingSQLite = true
	common.UsingMySQL = false
	common.UsingPostgreSQL = false
	common.RedisEnabled = false

	dsn := fmt.Sprintf("file:%s?mode=memory&cache=shared", t.Name())
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatalf("failed to open sqlite db: %v", err)
	}
	model.DB = db
	model.LOG_DB = db

	if err := db.AutoMigrate(&model.User{}, &model.Option{}); err != nil {
		t.Fatalf("failed to migrate tables: %v", err)
	}
	model.InitOptionMap()

	t.Cleanup(func() {
		sqlDB, err := db.DB()
		if err == nil {
			_ = sqlDB.Close()
		}
	})
	return db
}

func seedPlaygroundUser(t *testing.T, db *gorm.DB, userID int, role int, setting dto.UserSetting) {
	t.Helper()
	settingBytes, err := json.Marshal(setting)
	if err != nil {
		t.Fatalf("failed to marshal user setting: %v", err)
	}
	user := model.User{
		Id:       userID,
		Username: fmt.Sprintf("user-%d", userID),
		Password: "password",
		Role:     role,
		Status:   common.UserStatusEnabled,
		Group:    "default",
		Setting:  string(settingBytes),
	}
	if err := db.Create(&user).Error; err != nil {
		t.Fatalf("failed to create user: %v", err)
	}
}

func TestBuildPlaygroundRuntimePreviewMergesScopes(t *testing.T) {
	db := setupPlaygroundDefaultsTestDB(t)

	if err := model.UpdateOption("PlaygroundGlobalDefaults", `{"inputs":{"temperature":0.7,"top_p":0.8},"parameterEnabled":{"temperature":true,"top_p":true}}`); err != nil {
		t.Fatalf("failed to save global defaults: %v", err)
	}
	if err := model.UpdateOption("PlaygroundAdminDefaults", `{"inputs":{"temperature":0.4,"max_tokens":2048},"parameterEnabled":{"temperature":true,"max_tokens":true}}`); err != nil {
		t.Fatalf("failed to save admin defaults: %v", err)
	}

	seedPlaygroundUser(t, db, 1, common.RoleAdminUser, dto.UserSetting{
		PlaygroundApplyToRealAPI: true,
		PlaygroundDefaults: map[string]any{
			"inputs": map[string]any{
				"temperature": 0.2,
				"seed":        9,
				"model":       "ignored",
			},
			"parameterEnabled": map[string]any{
				"temperature": true,
				"seed":        true,
			},
			"systemPrompt": "ignored",
		},
	})

	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Set("id", 1)
	ctx.Set("role", common.RoleAdminUser)

	preview := buildPlaygroundRuntimePreview(ctx)
	if preview["applyToRealAPI"] != true {
		t.Fatalf("expected applyToRealAPI to be true, got %#v", preview["applyToRealAPI"])
	}
	mergedInputs, _ := preview["mergedInputs"].(map[string]any)
	injectWhenMissing, _ := preview["injectWhenMissing"].(map[string]any)
	if mergedInputs["temperature"] != 0.2 {
		t.Fatalf("expected personal temperature to win, got %#v", mergedInputs["temperature"])
	}
	if mergedInputs["max_tokens"] != 2048.0 && mergedInputs["max_tokens"] != 2048 {
		t.Fatalf("expected admin max_tokens to be present, got %#v", mergedInputs["max_tokens"])
	}
	if mergedInputs["seed"] != 9.0 && mergedInputs["seed"] != 9 {
		t.Fatalf("expected personal seed to be present, got %#v", mergedInputs["seed"])
	}
	if _, exists := mergedInputs["model"]; exists {
		t.Fatalf("did not expect personal model to survive sanitization")
	}
	if injectWhenMissing["temperature"] != 0.2 {
		t.Fatalf("expected temperature injection preview, got %#v", injectWhenMissing["temperature"])
	}
	if injectWhenMissing["seed"] != 9.0 && injectWhenMissing["seed"] != 9 {
		t.Fatalf("expected seed injection preview, got %#v", injectWhenMissing["seed"])
	}
}

func TestApplyPlaygroundRuntimeDefaultsFromStoredScopes(t *testing.T) {
	db := setupPlaygroundDefaultsTestDB(t)

	if err := model.UpdateOption("PlaygroundGlobalDefaults", `{"inputs":{"temperature":0.7,"top_p":0.6},"parameterEnabled":{"temperature":true,"top_p":true}}`); err != nil {
		t.Fatalf("failed to save global defaults: %v", err)
	}
	if err := model.UpdateOption("PlaygroundAdminDefaults", `{"inputs":{"max_tokens":4096},"parameterEnabled":{"max_tokens":true}}`); err != nil {
		t.Fatalf("failed to save admin defaults: %v", err)
	}

	seedPlaygroundUser(t, db, 2, common.RoleAdminUser, dto.UserSetting{
		PlaygroundApplyToRealAPI: true,
		PlaygroundDefaults: map[string]any{
			"inputs": map[string]any{
				"temperature": 0.25,
				"seed":        11,
			},
			"parameterEnabled": map[string]any{
				"temperature": true,
				"seed":        true,
			},
		},
	})

	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Set("id", 2)
	ctx.Set("role", common.RoleAdminUser)

	topP := 0.99
	req := &dto.GeneralOpenAIRequest{
		TopP: &topP,
	}
	applyPlaygroundRuntimeDefaults(ctx, req)

	if req.Temperature == nil || *req.Temperature != 0.25 {
		t.Fatalf("expected personal temperature to be injected, got %#v", req.Temperature)
	}
	if req.TopP == nil || *req.TopP != 0.99 {
		t.Fatalf("expected explicit top_p to be preserved, got %#v", req.TopP)
	}
	if req.MaxTokens == nil || *req.MaxTokens != 4096 {
		t.Fatalf("expected admin max_tokens to be injected, got %#v", req.MaxTokens)
	}
	if req.Seed == nil || *req.Seed != 11 {
		t.Fatalf("expected personal seed to be injected, got %#v", req.Seed)
	}
}

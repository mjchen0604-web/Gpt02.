package controller

import (
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
)

var playgroundAllowedInputKeys = map[string]struct{}{
	"model":             {},
	"group":             {},
	"temperature":       {},
	"top_p":             {},
	"frequency_penalty": {},
	"presence_penalty":  {},
	"seed":              {},
	"stream":            {},
	"imageEnabled":      {},
	"imageUrls":         {},
}

var playgroundAllowedParameterKeys = map[string]struct{}{
	"temperature":       {},
	"top_p":             {},
	"frequency_penalty": {},
	"presence_penalty":  {},
	"seed":              {},
}

var playgroundAllowedPersonalInputKeys = map[string]struct{}{
	"temperature":       {},
	"top_p":             {},
	"frequency_penalty": {},
	"presence_penalty":  {},
	"seed":              {},
}

var playgroundAllowedVisibility = map[string]struct{}{
	"off":    {},
	"admin":  {},
	"global": {},
}

type PlaygroundDefaultsRequest struct {
	Scope  string         `json:"scope"`
	Config map[string]any `json:"config"`
}

type PlaygroundVisibilityRequest struct {
	Key        string `json:"key"`
	Visibility string `json:"visibility"`
}

type PlaygroundApplyRequest struct {
	Enabled bool `json:"enabled"`
}

func sanitizePlaygroundConfig(raw map[string]any) map[string]any {
	out := map[string]any{}
	if raw == nil {
		return out
	}
	if inputs, ok := raw["inputs"].(map[string]any); ok {
		cleanInputs := map[string]any{}
		for key, value := range inputs {
			if _, allowed := playgroundAllowedInputKeys[key]; allowed {
				cleanInputs[key] = value
			}
		}
		if len(cleanInputs) > 0 {
			out["inputs"] = cleanInputs
		}
	}
	if enabled, ok := raw["parameterEnabled"].(map[string]any); ok {
		cleanEnabled := map[string]any{}
		for key, value := range enabled {
			if _, allowed := playgroundAllowedParameterKeys[key]; allowed {
				cleanEnabled[key] = value
			}
		}
		if len(cleanEnabled) > 0 {
			out["parameterEnabled"] = cleanEnabled
		}
	}
	return out
}

func sanitizePersonalPlaygroundConfig(raw map[string]any) map[string]any {
	out := map[string]any{}
	if raw == nil {
		return out
	}
	if inputs, ok := raw["inputs"].(map[string]any); ok {
		cleanInputs := map[string]any{}
		for key, value := range inputs {
			if _, allowed := playgroundAllowedPersonalInputKeys[key]; allowed {
				cleanInputs[key] = value
			}
		}
		if len(cleanInputs) > 0 {
			out["inputs"] = cleanInputs
		}
	}
	if enabled, ok := raw["parameterEnabled"].(map[string]any); ok {
		cleanEnabled := map[string]any{}
		for key, value := range enabled {
			if _, allowed := playgroundAllowedParameterKeys[key]; allowed {
				cleanEnabled[key] = value
			}
		}
		if len(cleanEnabled) > 0 {
			out["parameterEnabled"] = cleanEnabled
		}
	}
	return out
}

func decodePlaygroundConfig(optionKey string) map[string]any {
	common.OptionMapRWMutex.RLock()
	raw := common.OptionMap[optionKey]
	common.OptionMapRWMutex.RUnlock()
	if strings.TrimSpace(raw) == "" {
		return map[string]any{}
	}
	var parsed map[string]any
	if err := common.UnmarshalJsonStr(raw, &parsed); err != nil {
		return map[string]any{}
	}
	return sanitizePlaygroundConfig(parsed)
}

func currentPlaygroundVisibility(optionKey string) string {
	common.OptionMapRWMutex.RLock()
	value := strings.ToLower(strings.TrimSpace(common.OptionMap[optionKey]))
	common.OptionMapRWMutex.RUnlock()
	if _, ok := playgroundAllowedVisibility[value]; ok {
		return value
	}
	return "off"
}

func GetPlaygroundConfig(c *gin.Context) {
	role := c.GetInt("role")
	userID := c.GetInt("id")
	adminDefaults := map[string]any{}
	if role >= common.RoleAdminUser {
		adminDefaults = decodePlaygroundConfig("PlaygroundAdminDefaults")
	}
	userSettings, _ := model.GetUserSetting(userID, false)
	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": "",
		"data": gin.H{
			"globalDefaults":          decodePlaygroundConfig("PlaygroundGlobalDefaults"),
			"adminDefaults":           adminDefaults,
			"personalDefaults":        sanitizePersonalPlaygroundConfig(userSettings.PlaygroundDefaults),
			"applyToRealAPI":          userSettings.PlaygroundApplyToRealAPI,
			"runtimeDefaultsPreview":  buildPlaygroundRuntimePreview(c),
			"debugVisibility":         currentPlaygroundVisibility("PlaygroundDebugVisibility"),
			"customRequestVisibility": currentPlaygroundVisibility("PlaygroundCustomRequestVisibility"),
		},
	})
}

func SavePlaygroundDefaults(c *gin.Context) {
	var req PlaygroundDefaultsRequest
	if err := common.DecodeJson(c.Request.Body, &req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "invalid payload"})
		return
	}
	scope := strings.ToLower(strings.TrimSpace(req.Scope))
	optionKey := ""
	switch scope {
	case "personal":
		userID := c.GetInt("id")
		user, err := model.GetUserById(userID, true)
		if err != nil {
			common.ApiError(c, err)
			return
		}
		cleanConfig := sanitizePersonalPlaygroundConfig(req.Config)
		userSetting := user.GetSetting()
		userSetting.PlaygroundDefaults = cleanConfig
		user.SetSetting(userSetting)
		if err := user.Update(false); err != nil {
			common.ApiError(c, err)
			return
		}
		c.JSON(http.StatusOK, gin.H{"success": true, "message": "", "data": cleanConfig})
		return
	case "global":
		if c.GetInt("role") < common.RoleAdminUser {
			c.JSON(http.StatusForbidden, gin.H{"success": false, "message": "admin only"})
			return
		}
		optionKey = "PlaygroundGlobalDefaults"
	case "admin":
		if c.GetInt("role") < common.RoleAdminUser {
			c.JSON(http.StatusForbidden, gin.H{"success": false, "message": "admin only"})
			return
		}
		optionKey = "PlaygroundAdminDefaults"
	default:
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "scope must be personal, global, or admin"})
		return
	}
	cleanConfig := sanitizePlaygroundConfig(req.Config)
	jsonBytes, err := common.Marshal(cleanConfig)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "failed to encode config"})
		return
	}
	if err := model.UpdateOption(optionKey, string(jsonBytes)); err != nil {
		common.ApiError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "message": "", "data": cleanConfig})
}

func SavePlaygroundVisibility(c *gin.Context) {
	var req PlaygroundVisibilityRequest
	if err := common.DecodeJson(c.Request.Body, &req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "invalid payload"})
		return
	}
	visibility := strings.ToLower(strings.TrimSpace(req.Visibility))
	if _, ok := playgroundAllowedVisibility[visibility]; !ok {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "visibility must be off, admin, or global"})
		return
	}
	key := strings.ToLower(strings.TrimSpace(req.Key))
	optionKey := ""
	switch key {
	case "debug", "debug_panel":
		optionKey = "PlaygroundDebugVisibility"
	case "custom_request", "custom_request_mode":
		optionKey = "PlaygroundCustomRequestVisibility"
	default:
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "key must be debug or custom_request"})
		return
	}
	if err := model.UpdateOption(optionKey, visibility); err != nil {
		common.ApiError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "message": "", "data": gin.H{"key": key, "visibility": visibility}})
}

func SavePlaygroundApplyToRealAPI(c *gin.Context) {
	var req PlaygroundApplyRequest
	if err := common.DecodeJson(c.Request.Body, &req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "invalid payload"})
		return
	}
	userID := c.GetInt("id")
	user, err := model.GetUserById(userID, true)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	userSetting := user.GetSetting()
	userSetting.PlaygroundApplyToRealAPI = req.Enabled
	user.SetSetting(userSetting)
	if err := user.Update(false); err != nil {
		common.ApiError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"message": "",
		"data": gin.H{
			"enabled": req.Enabled,
		},
	})
}

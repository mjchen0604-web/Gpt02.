package controller

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

const (
	defaultChatCoreInternalHost = "127.0.0.1"
	defaultChatCoreInternalPort = "1455"
)

func embeddedChatBaseURL() string {
	host := strings.TrimSpace(os.Getenv("CHATCORE_INTERNAL_CHAT_HOST"))
	if host == "" {
		host = defaultChatCoreInternalHost
	}
	port := strings.TrimSpace(os.Getenv("CHATCORE_INTERNAL_CHAT_PORT"))
	if port == "" {
		port = defaultChatCoreInternalPort
	}
	return fmt.Sprintf("http://%s:%s", host, port)
}

func embeddedChatHTTPClient() *http.Client {
	return &http.Client{Timeout: 45 * time.Second}
}

func proxyEmbeddedChat(c *gin.Context, method string, path string, body io.Reader, contentType string) {
	req, err := http.NewRequestWithContext(c.Request.Context(), method, embeddedChatBaseURL()+path, body)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{
			"success": false,
			"message": "failed to build embedded chat request: " + err.Error(),
		})
		return
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}

	resp, err := embeddedChatHTTPClient().Do(req)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{
			"success": false,
			"message": "embedded chat is unavailable: " + err.Error(),
		})
		return
	}
	defer resp.Body.Close()

	payload, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		c.JSON(http.StatusBadGateway, gin.H{
			"success": false,
			"message": "failed to read embedded chat response: " + readErr.Error(),
		})
		return
	}

	responseType := resp.Header.Get("Content-Type")
	if responseType == "" {
		responseType = "application/json"
	}
	c.Data(resp.StatusCode, responseType, payload)
}

func GetEmbeddedChatHealth(c *gin.Context) {
	proxyEmbeddedChat(c, http.MethodGet, "/api/health", nil, "")
}

func GetEmbeddedChatAccounts(c *gin.Context) {
	proxyEmbeddedChat(c, http.MethodGet, "/api/accounts", nil, "")
}

func GetEmbeddedChatModels(c *gin.Context) {
	proxyEmbeddedChat(c, http.MethodGet, "/api/models", nil, "")
}

func GetEmbeddedChatConfig(c *gin.Context) {
	proxyEmbeddedChat(c, http.MethodGet, "/api/config", nil, "")
}

func GetEmbeddedChatLogs(c *gin.Context) {
	lines := strings.TrimSpace(c.Query("lines"))
	path := "/api/logs"
	if lines != "" {
		path = path + "?lines=" + lines
	}
	proxyEmbeddedChat(c, http.MethodGet, path, nil, "")
}

func GetEmbeddedChatSettings(c *gin.Context) {
	proxyEmbeddedChat(c, http.MethodGet, "/api/settings", nil, "")
}

func SaveEmbeddedChatSettings(c *gin.Context) {
	payload := map[string]any{}
	if err := c.ShouldBindJSON(&payload); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"success": false,
			"message": "invalid JSON payload: " + err.Error(),
		})
		return
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"success": false,
			"message": "failed to encode JSON payload: " + err.Error(),
		})
		return
	}
	proxyEmbeddedChat(c, http.MethodPost, "/api/settings", bytes.NewReader(raw), "application/json")
}

func UploadEmbeddedChatAuths(c *gin.Context) {
	form, err := c.MultipartForm()
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"success": false,
			"message": "invalid multipart form: " + err.Error(),
		})
		return
	}

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if replace := strings.TrimSpace(c.PostForm("replace")); replace != "" {
		_ = writer.WriteField("replace", replace)
	}

	fileCount := 0
	for _, files := range form.File {
		for _, header := range files {
			src, openErr := header.Open()
			if openErr != nil {
				_ = writer.Close()
				c.JSON(http.StatusBadRequest, gin.H{
					"success": false,
					"message": "failed to open upload file: " + openErr.Error(),
				})
				return
			}

			part, createErr := writer.CreateFormFile("files", header.Filename)
			if createErr != nil {
				src.Close()
				_ = writer.Close()
				c.JSON(http.StatusBadRequest, gin.H{
					"success": false,
					"message": "failed to create upload part: " + createErr.Error(),
				})
				return
			}

			if _, copyErr := io.Copy(part, src); copyErr != nil {
				src.Close()
				_ = writer.Close()
				c.JSON(http.StatusBadRequest, gin.H{
					"success": false,
					"message": "failed to copy upload file: " + copyErr.Error(),
				})
				return
			}
			src.Close()
			fileCount++
		}
	}

	if fileCount == 0 {
		_ = writer.Close()
		c.JSON(http.StatusBadRequest, gin.H{
			"success": false,
			"message": "no files uploaded",
		})
		return
	}

	if err = writer.Close(); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"success": false,
			"message": "failed to finalize upload: " + err.Error(),
		})
		return
	}

	proxyEmbeddedChat(c, http.MethodPost, "/api/actions/upload_auths", bytes.NewReader(body.Bytes()), writer.FormDataContentType())
}

package middleware

import (
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestSiteAccessAuthAllowsHealthAndBlocksRoot(t *testing.T) {
	t.Setenv(siteAuthUserEnv, "admin")
	t.Setenv(siteAuthPasswordEnv, "secret")
	gin.SetMode(gin.TestMode)

	router := gin.New()
	router.Use(SiteAccessAuth())
	router.GET("/health", func(c *gin.Context) {
		c.String(http.StatusOK, "ok")
	})
	router.GET("/", func(c *gin.Context) {
		c.String(http.StatusOK, "root")
	})

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	resp := httptest.NewRecorder()
	router.ServeHTTP(resp, req)
	if resp.Code != http.StatusOK {
		t.Fatalf("expected /health to bypass auth, got %d", resp.Code)
	}

	req = httptest.NewRequest(http.MethodGet, "/", nil)
	resp = httptest.NewRecorder()
	router.ServeHTTP(resp, req)
	if resp.Code != http.StatusUnauthorized {
		t.Fatalf("expected / to require auth, got %d", resp.Code)
	}

	req = httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte("admin:secret")))
	resp = httptest.NewRecorder()
	router.ServeHTTP(resp, req)
	if resp.Code != http.StatusOK {
		t.Fatalf("expected authenticated request to pass, got %d", resp.Code)
	}
}

func TestSiteNoIndexHeader(t *testing.T) {
	t.Setenv(siteNoIndexEnv, "true")
	gin.SetMode(gin.TestMode)

	router := gin.New()
	router.Use(SiteNoIndex())
	router.GET("/", func(c *gin.Context) {
		c.String(http.StatusOK, "ok")
	})

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	resp := httptest.NewRecorder()
	router.ServeHTTP(resp, req)

	if got := resp.Header().Get("X-Robots-Tag"); got == "" {
		t.Fatal("expected X-Robots-Tag header to be set")
	}
}

func TestMain(m *testing.M) {
	gin.SetMode(gin.TestMode)
	os.Exit(m.Run())
}

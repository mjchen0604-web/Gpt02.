package middleware

import (
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
)

const (
	siteAuthUserEnv     = "IIFY_SITE_AUTH_USERNAME"
	siteAuthPasswordEnv = "IIFY_SITE_AUTH_PASSWORD"
	siteNoIndexEnv      = "IIFY_SITE_NOINDEX"
)

func SiteNoIndex() gin.HandlerFunc {
	enabled := common.GetEnvOrDefaultBool(siteNoIndexEnv, true)
	return func(c *gin.Context) {
		if enabled {
			c.Header("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet")
		}
		c.Next()
	}
}

func SiteAccessAuth() gin.HandlerFunc {
	username := strings.TrimSpace(common.GetEnvOrDefaultString(siteAuthUserEnv, ""))
	password := common.GetEnvOrDefaultString(siteAuthPasswordEnv, "")
	enabled := username != "" && password != ""

	return func(c *gin.Context) {
		if !enabled {
			c.Next()
			return
		}

		if c.Request.Method == http.MethodOptions {
			c.Next()
			return
		}

		path := c.Request.URL.Path
		if path == "/health" {
			c.Next()
			return
		}

		user, pass, ok := c.Request.BasicAuth()
		if ok && user == username && pass == password {
			c.Next()
			return
		}

		c.Header("WWW-Authenticate", `Basic realm="II.fy"`)
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
			"error": gin.H{
				"message": "site authentication required",
			},
		})
	}
}

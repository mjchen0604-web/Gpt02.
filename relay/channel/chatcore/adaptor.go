package chatcore

import (
	"errors"
	"fmt"
	"io"
	"net/http"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/relay/channel"
	relayclaude "github.com/QuantumNous/new-api/relay/channel/claude"
	relayopenai "github.com/QuantumNous/new-api/relay/channel/openai"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/types"

	"github.com/gin-gonic/gin"
)

const ChannelName = "chatcore"

type Adaptor struct{}

func (a *Adaptor) Init(info *relaycommon.RelayInfo) {}

func (a *Adaptor) GetRequestURL(info *relaycommon.RelayInfo) (string, error) {
	if info == nil {
		return "", errors.New("relay info is nil")
	}
	return relaycommon.GetFullRequestURL(info.ChannelBaseUrl, info.RequestURLPath, info.ChannelType), nil
}

func (a *Adaptor) SetupRequestHeader(c *gin.Context, req *http.Header, info *relaycommon.RelayInfo) error {
	channel.SetupApiRequestHeader(info, c, req)
	if info != nil && info.ApiKey != "" {
		req.Set("Authorization", "Bearer "+info.ApiKey)
	}
	return nil
}

func (a *Adaptor) ConvertOpenAIRequest(c *gin.Context, info *relaycommon.RelayInfo, request *dto.GeneralOpenAIRequest) (any, error) {
	if request == nil {
		return nil, errors.New("request is nil")
	}
	return request, nil
}

func (a *Adaptor) ConvertClaudeRequest(c *gin.Context, info *relaycommon.RelayInfo, request *dto.ClaudeRequest) (any, error) {
	if request == nil {
		return nil, errors.New("request is nil")
	}
	return request, nil
}

func (a *Adaptor) ConvertGeminiRequest(c *gin.Context, info *relaycommon.RelayInfo, request *dto.GeminiChatRequest) (any, error) {
	return nil, errors.New("chatcore channel: gemini relay is not supported")
}

func (a *Adaptor) ConvertRerankRequest(c *gin.Context, relayMode int, request dto.RerankRequest) (any, error) {
	return nil, errors.New("chatcore channel: rerank is not supported")
}

func (a *Adaptor) ConvertEmbeddingRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.EmbeddingRequest) (any, error) {
	return nil, errors.New("chatcore channel: embeddings are not supported")
}

func (a *Adaptor) ConvertAudioRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.AudioRequest) (io.Reader, error) {
	return nil, errors.New("chatcore channel: audio endpoints are not supported")
}

func (a *Adaptor) ConvertImageRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.ImageRequest) (any, error) {
	return nil, errors.New("chatcore channel: image endpoints are not supported")
}

func (a *Adaptor) ConvertOpenAIResponsesRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.OpenAIResponsesRequest) (any, error) {
	return nil, errors.New("chatcore channel: /v1/responses is not supported by the embedded chat core")
}

func (a *Adaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (any, error) {
	return channel.DoApiRequest(a, c, info, requestBody)
}

func (a *Adaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (usage any, err *types.NewAPIError) {
	if info == nil {
		return nil, types.NewOpenAIError(errors.New("relay info is nil"), types.ErrorCodeInvalidRequest, http.StatusBadRequest)
	}
	switch info.RelayFormat {
	case types.RelayFormatClaude:
		adaptor := relayclaude.Adaptor{}
		return adaptor.DoResponse(c, resp, info)
	case types.RelayFormatOpenAI, types.RelayFormatOpenAIAudio, types.RelayFormatOpenAIImage:
		fallthrough
	case types.RelayFormatEmbedding:
		fallthrough
	case types.RelayFormatOpenAIRealtime:
		fallthrough
	case types.RelayFormatOpenAIResponses:
		fallthrough
	case types.RelayFormatOpenAIResponsesCompaction:
		adaptor := relayopenai.Adaptor{}
		return adaptor.DoResponse(c, resp, info)
	default:
		return nil, types.NewOpenAIError(fmt.Errorf("chatcore channel: unsupported relay format %s", info.RelayFormat), types.ErrorCodeInvalidRequest, http.StatusBadRequest)
	}
}

func (a *Adaptor) GetModelList() []string {
	return []string{}
}

func (a *Adaptor) GetChannelName() string {
	return ChannelName
}

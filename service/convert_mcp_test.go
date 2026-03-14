package service

import (
	"testing"

	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

func TestClaudeMCPToolNamesAreSanitizedAndRestored(t *testing.T) {
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{},
	}
	req := dto.ClaudeRequest{
		Model: "gpt-5.4-fast-low",
		Tools: []dto.Tool{
			{
				Name:        "mcp__CherryHub__list",
				Description: "List docs",
				InputSchema: map[string]interface{}{"type": "object"},
			},
		},
	}

	openAIReq, err := ClaudeToOpenAIRequest(req, info)
	if err != nil {
		t.Fatalf("ClaudeToOpenAIRequest failed: %v", err)
	}
	if len(openAIReq.Tools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(openAIReq.Tools))
	}

	safeName := openAIReq.Tools[0].Function.Name
	if safeName == "mcp__CherryHub__list" {
		t.Fatalf("expected sanitized tool name, got original %q", safeName)
	}

	resp := &dto.OpenAITextResponse{
		Model: "gpt-5.4-fast-low",
	}
	message := dto.Message{Role: "assistant"}
	message.SetToolCalls([]dto.ToolCallRequest{
		{
			ID:   "call_1",
			Type: "function",
			Function: dto.FunctionRequest{
				Name:      safeName,
				Arguments: `{"q":"x"}`,
			},
		},
	})
	resp.Choices = []dto.OpenAITextResponseChoice{
		{
			FinishReason: "tool_calls",
			Message:      message,
		},
	}

	claudeResp := ResponseOpenAI2Claude(resp, info)
	if len(claudeResp.Content) != 1 {
		t.Fatalf("expected 1 content block, got %d", len(claudeResp.Content))
	}
	if claudeResp.Content[0].Name != "mcp__CherryHub__list" {
		t.Fatalf("expected restored tool name, got %q", claudeResp.Content[0].Name)
	}
}

package ratio_setting

import "testing"

func TestGetLongContextPricingMultiplier(t *testing.T) {
	tests := []struct {
		name        string
		model       string
		promptToken int
		want        float64
	}{
		{
			name:        "below threshold no surcharge",
			model:       "gpt-5.4-low",
			promptToken: 272000,
			want:        1,
		},
		{
			name:        "gpt54 standard long context is four times",
			model:       "gpt-5.4-medium",
			promptToken: 300000,
			want:        4,
		},
		{
			name:        "gpt54 turbo long context is four times",
			model:       "gpt-5.4-fast-low",
			promptToken: 300000,
			want:        4,
		},
		{
			name:        "other family unchanged",
			model:       "gpt-5.2-high",
			promptToken: 300000,
			want:        1,
		},
		{
			name:        "above supported range unchanged",
			model:       "gpt-5.4-fast-low",
			promptToken: 1000001,
			want:        1,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := GetLongContextPricingMultiplier(tt.model, tt.promptToken); got != tt.want {
				t.Fatalf("GetLongContextPricingMultiplier(%q, %d) = %v, want %v", tt.model, tt.promptToken, got, tt.want)
			}
		})
	}
}

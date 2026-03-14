package ratio_setting

import "strings"

const (
	LongContextThresholdTokens = 272000
	LongContextMaxTokens       = 1000000
)

func GetLongContextThresholdTokens() int {
	return LongContextThresholdTokens
}

func GetLongContextMaxTokens() int {
	return LongContextMaxTokens
}

func SupportsLongContextSurcharge(modelName string) bool {
	normalized := strings.ToLower(FormatMatchingModelName(modelName))
	return strings.HasPrefix(normalized, "gpt-5.4")
}

func IsTurboPerformanceModel(modelName string) bool {
	normalized := strings.ToLower(FormatMatchingModelName(modelName))
	return strings.Contains(normalized, "-fast") || strings.Contains(normalized, "-turbo")
}

func GetLongContextPricingMultiplier(modelName string, promptTokens int) float64 {
	if promptTokens <= LongContextThresholdTokens || promptTokens > LongContextMaxTokens {
		return 1
	}
	if !SupportsLongContextSurcharge(modelName) {
		return 1
	}
	if IsTurboPerformanceModel(modelName) {
		return 4
	}
	return 2
}

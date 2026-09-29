import { geminiProvider } from "./gemini"
import { mockProvider } from "./mock"
import type { AIProvider } from "./types"

export function getAI(): AIProvider {
  return process.env.AI_PROVIDER === "gemini" && process.env.GEMINI_API_KEY ? geminiProvider : mockProvider
}

export * from "./types"

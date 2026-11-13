import { describe, it, expect } from "vitest"
import { isLikelySilenceHallucination } from "@/features/chat/ChatInput"

/**
 * Whisper does not return "" for silence; it returns something plausible.
 * Measured live on this project: a capture from a machine with no microphone
 * transcribed as "you you" and was inserted into the composer as if the user
 * had spoken it. These cases pin the guard without swallowing real commands.
 */
describe("isLikelySilenceHallucination", () => {
  it.each(["you", "You.", "you you", "thank you", "Thank you!", "thanks", "[BLANK_AUDIO]"])(
    "treats %s as invented text",
    (text) => {
      expect(isLikelySilenceHallucination(text)).toBe(true)
    }
  )

  it.each(["", "   ", "...", "!!!"])("treats %s as silence", (text) => {
    expect(isLikelySilenceHallucination(text)).toBe(true)
  })

  it.each([
    "yes",
    "no",
    "stop",
    "you are wrong about the second step",
    "thank you for the summary, now shorten it",
  ])("keeps %s, which is something a user would actually say", (text) => {
    expect(isLikelySilenceHallucination(text)).toBe(false)
  })
})

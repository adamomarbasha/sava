"use client";

import { useState, useEffect } from "react";
import { Spinner } from "./UI";

interface TranscriptEntry {
  text: string;
  start: number;
  duration: number;
}

interface CommentEntry {
  text: string;
  author: string;
  like_count: number;
}

interface VideoMetadata {
  title: string;
  description?: string;
  author: string;
  transcript?: TranscriptEntry[];
  comments?: CommentEntry[];
  thumbnail_url?: string;
  url: string;
}

interface AISummaryBoxProps {
  metadata: VideoMetadata;
}

export default function AISummaryBox({ metadata }: AISummaryBoxProps) {
  const [summary, setSummary] = useState<string | null>(null);
  const [displayedText, setDisplayedText] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isTyping, setIsTyping] = useState<boolean>(false);

  useEffect(() => {
    const fetchSummary = async () => {
      setLoading(true);
      setError(null);

      try {
        const response = await fetch("/api/ai-summary", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            title: metadata.title,
            description: metadata.description,
            author: metadata.author,
            transcript: metadata.transcript,
            comments: metadata.comments,
            thumbnail_url: metadata.thumbnail_url,
          }),
        });

        const data = await response.json();

        if (data.success) {
          setSummary(data.summary);
          setIsTyping(true);
        } else {
          setError(data.error || "Failed to generate AI summary.");
        }
      } catch (err: any) {
        console.error("Error fetching AI summary:", err);
        setError("Network error: Unable to reach the AI summary service.");
      } finally {
        setLoading(false);
      }
    };

    fetchSummary();
  }, [metadata]);

  useEffect(() => {
    if (!summary || !isTyping) return;

    let currentIndex = 0;
    setDisplayedText("");

    const typewriterInterval = setInterval(() => {
      if (currentIndex < summary.length) {
        setDisplayedText(summary.slice(0, currentIndex + 1));
        currentIndex++;
      } else {
        setIsTyping(false);
        clearInterval(typewriterInterval);
      }
    }, 10);

    return () => clearInterval(typewriterInterval);
  }, [summary, isTyping]);

  if (loading) {
    return (
      <div className="text-center py-8">
        <Spinner className="w-8 h-8 text-white mx-auto mb-4" />
        <p className="text-white/80 text-sm" style={{ fontFamily: "Minecraft, monospace" }}>
          Generating AI summary...
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-6">
        <div className="mb-4">
          <svg
            className="w-12 h-12 text-red-400 mx-auto"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
        </div>
        <p className="text-red-200 text-sm mb-4" style={{ fontFamily: "Minecraft, monospace" }}>
          {error}
        </p>
        <button
          onClick={() => window.location.reload()}
          className="px-4 py-2 bg-white/20 hover:bg-white/30 rounded-lg text-white text-sm transition-colors duration-200"
          style={{ fontFamily: "Minecraft, monospace" }}
        >
          Try Again
        </button>
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="text-center py-6">
        <p className="text-white/60 text-sm" style={{ fontFamily: "Minecraft, monospace" }}>
          No summary available.
        </p>
      </div>
    );
  }

  return (
    <div className="text-base text-white leading-relaxed text-left" style={{ fontFamily: "Minecraft, monospace" }}>
      {displayedText}
      {isTyping && <span className="animate-pulse">▊</span>}
    </div>
  );
}

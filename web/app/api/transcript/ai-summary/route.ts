import { NextRequest, NextResponse } from "next/server";
import { generateAISummary, VideoMetadata, TranscriptEntry, CommentEntry } from "@/lib/gemini";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface RequestBody {
  title: string;
  description?: string;
  author: string;
  transcript?: TranscriptEntry[];
  comments?: CommentEntry[];
  thumbnail_url?: string;
}

export async function POST(req: NextRequest) {
  try {
    const body: RequestBody = await req.json();

    if (!body.title || !body.author) {
      return NextResponse.json(
        {
          success: false,
          error: "Missing required fields: title and author are required.",
        },
        { status: 400 }
      );
    }

    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
      return NextResponse.json(
        {
          success: false,
          error: "Server configuration error: GEMINI_API_KEY is not set.",
        },
        { status: 500 }
      );
    }

    const metadata: VideoMetadata = {
      title: body.title,
      description: body.description,
      author: body.author,
      transcript: body.transcript,
      comments: body.comments,
      thumbnail_url: body.thumbnail_url,
    };

    const hasTranscript = metadata.transcript && metadata.transcript.length > 0;

    if (!hasTranscript && !metadata.description && (!metadata.comments || metadata.comments.length === 0)) {
      return NextResponse.json(
        {
          success: false,
          error: "Insufficient data: Transcript not available. Please provide a description or comments for fallback summary.",
        },
        { status: 400 }
      );
    }

    const result = await generateAISummary(metadata, apiKey);

    if (!result.success) {
      return NextResponse.json(
        {
          success: false,
          error: result.error || "Failed to generate AI summary.",
        },
        { status: 500 }
      );
    }

    return NextResponse.json(
      {
        success: true,
        summary: result.summary,
      },
      { status: 200 }
    );
  } catch (error: any) {
    console.error("Error in /api/ai-summary:", error);
    return NextResponse.json(
      {
        success: false,
        error: `Internal server error: ${error?.message || "Unknown error"}`,
      },
      { status: 500 }
    );
  }
}

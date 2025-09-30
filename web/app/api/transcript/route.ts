import { NextRequest, NextResponse } from 'next/server';

const API_BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';

interface TranscriptRequest {
  video_url_or_id: string;
  languages?: string[];
  preserve_formatting?: boolean;
}

interface TranscriptResponse {
  success: boolean;
  transcript?: Array<{
    text: string;
    start: number;
    duration: number;
  }>;
  error?: string;
  video_id?: string;
  language?: string;
}

export async function POST(request: NextRequest) {
  try {
    const body: TranscriptRequest = await request.json();
    
    if (!body.video_url_or_id) {
      return NextResponse.json(
        { error: 'video_url_or_id is required' },
        { status: 400 }
      );
    }

    const response = await fetch(`${API_BASE_URL}/api/transcript`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorData = await response.json();
      return NextResponse.json(
        { error: errorData.detail || 'Failed to fetch transcript' },
        { status: response.status }
      );
    }

    const data: TranscriptResponse = await response.json();
    return NextResponse.json(data);

  } catch (error) {
    console.error('Transcript API error:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const videoId = searchParams.get('video_id');
    const languages = searchParams.get('languages');
    const preserveFormatting = searchParams.get('preserve_formatting') === 'true';

    if (!videoId) {
      return NextResponse.json(
        { error: 'video_id parameter is required' },
        { status: 400 }
      );
    }

    const queryParams = new URLSearchParams();
    if (languages) queryParams.set('languages', languages);
    if (preserveFormatting) queryParams.set('preserve_formatting', 'true');

    const queryString = queryParams.toString();
    const url = `${API_BASE_URL}/api/transcript/${videoId}${queryString ? `?${queryString}` : ''}`;

    const response = await fetch(url);

    if (!response.ok) {
      const errorData = await response.json();
      return NextResponse.json(
        { error: errorData.detail || 'Failed to fetch transcript' },
        { status: response.status }
      );
    }

    const data: TranscriptResponse = await response.json();
    return NextResponse.json(data);

  } catch (error) {
    console.error('Transcript API error:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}

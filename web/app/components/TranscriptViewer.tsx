'use client';

import React, { useState, useEffect } from 'react';

interface TranscriptEntry {
  text: string;
  start: number;
  duration: number;
}

interface TranscriptData {
  success: boolean;
  transcript?: TranscriptEntry[];
  error?: string;
  video_id?: string;
  language?: string;
}

interface TranscriptViewerProps {
  videoUrlOrId: string;
  onTranscriptLoaded?: (transcript: TranscriptEntry[]) => void;
  onError?: (error: string) => void;
}

export default function TranscriptViewer({ 
  videoUrlOrId, 
  onTranscriptLoaded, 
  onError 
}: TranscriptViewerProps) {
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);

  const fetchTranscript = async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/transcript', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          video_url_or_id: videoUrlOrId,
          languages: ['en'],
          preserve_formatting: false
        }),
      });

      const data: TranscriptData = await response.json();

      if (data.success && data.transcript) {
        setTranscript(data.transcript);
        onTranscriptLoaded?.(data.transcript);
      } else {
        const errorMsg = data.error || 'Failed to fetch transcript';
        setError(errorMsg);
        onError?.(errorMsg);
      }
    } catch (err) {
      const errorMsg = 'Network error fetching transcript';
      setError(errorMsg);
      onError?.(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  const formatTime = (seconds: number): string => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const getCurrentTranscriptEntry = (): TranscriptEntry | null => {
    if (!transcript.length) return null;
    
    for (let i = transcript.length - 1; i >= 0; i--) {
      if (currentTime >= transcript[i].start) {
        return transcript[i];
      }
    }
    return transcript[0];
  };

  const handleTimeUpdate = (time: number) => {
    setCurrentTime(time);
  };

  useEffect(() => {
    if (videoUrlOrId) {
      fetchTranscript();
    }
  }, [videoUrlOrId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center p-4">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
        <span className="ml-2">Loading transcript...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
        <h3 className="text-red-800 font-semibold">Transcript Error</h3>
        <p className="text-red-600 mt-1">{error}</p>
        <button
          onClick={fetchTranscript}
          className="mt-2 px-3 py-1 bg-red-600 text-white rounded hover:bg-red-700"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!transcript.length) {
    return (
      <div className="p-4 bg-gray-50 border border-gray-200 rounded-lg">
        <p className="text-gray-600">No transcript available for this video.</p>
      </div>
    );
  }

  const currentEntry = getCurrentTranscriptEntry();

  return (
    <div className="space-y-4">
      {currentEntry && (
        <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
          <div className="text-sm text-blue-600 mb-1">
            Currently speaking at {formatTime(currentTime)}
          </div>
          <div className="text-blue-800 font-medium">
            {currentEntry.text}
          </div>
        </div>
      )}

      <div className="max-h-96 overflow-y-auto border border-gray-200 rounded-lg">
        <div className="p-2 bg-gray-50 border-b">
          <h3 className="font-semibold text-gray-800">
            Full Transcript ({transcript.length} entries)
          </h3>
        </div>
        <div className="divide-y divide-gray-100">
          {transcript.map((entry, index) => (
            <div
              key={index}
              className={`p-3 hover:bg-gray-50 cursor-pointer transition-colors ${
                currentEntry === entry ? 'bg-blue-50 border-l-4 border-blue-400' : ''
              }`}
              onClick={() => handleTimeUpdate(entry.start)}
            >
              <div className="flex items-start space-x-3">
                <span className="text-xs text-gray-500 font-mono min-w-[60px]">
                  {formatTime(entry.start)}
                </span>
                <span className="text-sm text-gray-800 flex-1">
                  {entry.text}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="text-xs text-gray-500 space-y-1">
        <div>Total duration: {formatTime(transcript[transcript.length - 1]?.start + transcript[transcript.length - 1]?.duration || 0)}</div>
        <div>Total words: {transcript.reduce((acc, entry) => acc + entry.text.split(' ').length, 0)}</div>
      </div>
    </div>
  );
}

export function useTranscript(videoUrlOrId: string) {
  const [data, setData] = useState<TranscriptData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTranscript = async () => {
    if (!videoUrlOrId) return;

    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/transcript', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          video_url_or_id: videoUrlOrId,
          languages: ['en'],
          preserve_formatting: false
        }),
      });

      const result: TranscriptData = await response.json();
      setData(result);
      
      if (!result.success) {
        setError(result.error || 'Failed to fetch transcript');
      }
    } catch (err) {
      setError('Network error fetching transcript');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTranscript();
  }, [videoUrlOrId]);

  return {
    data,
    loading,
    error,
    refetch: fetchTranscript
  };
}

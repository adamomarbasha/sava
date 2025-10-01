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
  onSeekTo?: (timestamp: number) => void;
}

export default function TranscriptViewer({ 
  videoUrlOrId, 
  onTranscriptLoaded, 
  onError,
  onSeekTo
}: TranscriptViewerProps) {
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [searchTerm, setSearchTerm] = useState('');
  const [filteredTranscript, setFilteredTranscript] = useState<TranscriptEntry[]>([]);

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
        setFilteredTranscript(data.transcript);
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

  useEffect(() => {
    if (!searchTerm.trim()) {
      setFilteredTranscript(transcript);
    } else {
      const filtered = transcript.filter(entry => 
        entry.text.toLowerCase().includes(searchTerm.toLowerCase())
      );
      setFilteredTranscript(filtered);
    }
  }, [searchTerm, transcript]);

  const handleTimeUpdate = (time: number) => {
    setCurrentTime(time);
    onSeekTo?.(time);
  };

  useEffect(() => {
    if (videoUrlOrId) {
      fetchTranscript();
    }
  }, [videoUrlOrId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <div className="flex items-center space-x-3">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-500 border-t-transparent"></div>
          <span className="text-gray-600 font-medium">Loading transcript...</span>
        </div>
      </div>
    );
  }

  if (error) {
    const isNoSubtitlesError = error.includes('subtitles disabled') || 
                               error.includes('No subtitles are available') ||
                               error.includes('music videos') ||
                               error.includes('instrumentals') ||
                               error.includes('content without speech');
    
    if (isNoSubtitlesError) {
      return (
        <div className="p-6 bg-amber-50 border border-amber-200 rounded-xl">
          <div className="flex items-center space-x-2 mb-3">
            <svg className="w-5 h-5 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <h3 className="text-amber-800 font-semibold">No Subtitles Available</h3>
          </div>
          <p className="text-amber-700 mb-4">{error}</p>
          <div className="text-sm text-amber-600">
            <p>This is normal for:</p>
            <ul className="list-disc list-inside mt-2 space-y-1">
              <li>Music videos and instrumentals</li>
              <li>Content without speech</li>
              <li>Videos where creators disabled subtitles</li>
            </ul>
          </div>
        </div>
      );
    }
    
    return (
      <div className="p-6 bg-red-50 border border-red-200 rounded-xl">
        <div className="flex items-center space-x-2 mb-3">
          <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <h3 className="text-red-800 font-semibold">Transcript Error</h3>
        </div>
        <p className="text-red-600 mb-4">{error}</p>
        <button
          onClick={fetchTranscript}
          className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors duration-200 font-medium"
        >
          Try Again
        </button>
      </div>
    );
  }

  if (!transcript.length) {
    return (
      <div className="p-6 bg-gray-50 border border-gray-200 rounded-xl text-center">
        <svg className="w-12 h-12 text-gray-400 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <p className="text-gray-600 font-medium">No transcript available for this video.</p>
      </div>
    );
  }

  const currentEntry = getCurrentTranscriptEntry();

  return (
    <div className="space-y-6">
      {currentEntry && (
        <div className="p-4 bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-xl shadow-sm">
          <div className="flex items-center space-x-2 mb-2">
            <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
            <span className="text-sm text-blue-600 font-medium">
              Currently speaking at {formatTime(currentTime)}
            </span>
          </div>
          <div className="text-blue-900 font-medium text-lg leading-relaxed">
            {currentEntry.text}
          </div>
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-gray-800 text-lg">
              Full Transcript
            </h3>
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-500 bg-gray-200 px-3 py-1 rounded-full">
                {filteredTranscript.length} of {transcript.length} entries
              </span>
              <button
                onClick={() => {
                  const transcriptText = transcript.map(entry => entry.text).join(' ');
                  navigator.clipboard.writeText(transcriptText).then(() => {
                    const button = document.querySelector('[data-copy-transcript-button]');
                    if (button) {
                      const originalText = button.textContent;
                      button.textContent = 'Copied!';
                      button.classList.add('bg-green-500', 'text-white');
                      setTimeout(() => {
                        button.textContent = originalText;
                        button.classList.remove('bg-green-500', 'text-white');
                      }, 2000);
                    }
                  }).catch(() => {
                    alert('Failed to copy transcript');
                  });
                }}
                data-copy-transcript-button
                className="px-3 py-1.5 bg-blue-500 hover:bg-blue-600 text-white text-sm rounded-lg transition-all duration-200 flex items-center gap-2 font-medium"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                Copy Text
              </button>
            </div>
          </div>
          
          {/* Search Input */}
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <svg className="h-5 w-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <input
              type="text"
              placeholder="Search transcript..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-lg leading-5 bg-white placeholder-gray-500 focus:outline-none focus:placeholder-gray-400 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 text-sm"
            />
            {searchTerm && (
              <button
                onClick={() => setSearchTerm('')}
                className="absolute inset-y-0 right-0 pr-3 flex items-center"
              >
                <svg className="h-5 w-5 text-gray-400 hover:text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {filteredTranscript.length === 0 && searchTerm ? (
            <div className="p-8 text-center">
              <svg className="w-12 h-12 text-gray-400 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <p className="text-gray-500 font-medium">No results found for "{searchTerm}"</p>
              <p className="text-gray-400 text-sm mt-1">Try a different search term</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {filteredTranscript.map((entry, index) => {
                const isCurrentEntry = currentEntry === entry;
                const highlightText = (text: string, searchTerm: string) => {
                  if (!searchTerm.trim()) return text;
                  
                  const escapedSearchTerm = searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                  const regex = new RegExp(`(${escapedSearchTerm})`, 'gi');
                  
                  const parts = text.split(regex);
                  
                  return parts.map((part, index) => {
                    if (part.toLowerCase() === searchTerm.toLowerCase()) {
                      return (
                        <mark key={index} className="bg-blue-200 text-blue-900">
                          {part}
                        </mark>
                      );
                    }
                    return part;
                  });
                };

                return (
                  <div
                    key={index}
                    className={`p-4 hover:bg-gray-50 cursor-pointer transition-all duration-200 group ${
                      isCurrentEntry 
                        ? 'bg-blue-50 border-l-4 border-blue-500 shadow-sm' 
                        : 'hover:shadow-sm'
                    }`}
                    onClick={() => handleTimeUpdate(entry.start)}
                    title="Click to jump to this timestamp in the video"
                  >
                    <div className="flex items-start space-x-4">
                      <span className="text-xs text-gray-500 font-mono min-w-[70px] bg-gray-100 px-2 py-1 rounded group-hover:bg-blue-100 group-hover:text-blue-700 transition-colors duration-200">
                        {formatTime(entry.start)}
                      </span>
                      <span className="text-sm text-gray-800 flex-1 leading-relaxed group-hover:text-gray-900 transition-colors duration-200">
                        {highlightText(entry.text, searchTerm)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div className="flex items-center space-x-2">
            <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span className="text-gray-600 font-medium">
              Duration: {formatTime(transcript[transcript.length - 1]?.start + transcript[transcript.length - 1]?.duration || 0)}
            </span>
          </div>
          <div className="flex items-center space-x-2">
            <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <span className="text-gray-600 font-medium">
              Words: {filteredTranscript.reduce((acc, entry) => acc + entry.text.split(' ').length, 0)}
              {searchTerm && filteredTranscript.length !== transcript.length && (
                <span className="text-blue-600 ml-1">(filtered)</span>
              )}
            </span>
          </div>
        </div>
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

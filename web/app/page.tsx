"use client";

import { useState, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

interface Bookmark {
  id: number;
  url: string;
  note?: string;
  platform?: string;
  created_at: string;
}

export default function Home() {
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);

  // Fetch existing bookmarks on page load
  useEffect(() => {
    fetch(`${API_BASE}/bookmarks`)
      .then((res) => res.json())
      .then((data) => setBookmarks(data));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await fetch(`${API_BASE}/bookmarks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, note }),
    });
    const newBookmark = await res.json();
    setBookmarks([newBookmark, ...bookmarks]); // prepend new one
    setUrl("");
    setNote("");
  };

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <h1 className="text-3xl font-bold mb-6">📌 My Bookmarks</h1>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-full max-w-md">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Paste a YouTube/Instagram/TikTok URL"
          className="border p-2 rounded"
          required
        />
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Add a note (optional)"
          className="border p-2 rounded"
        />
        <button type="submit" className="bg-blue-500 text-white p-2 rounded">
          Save Bookmark
        </button>
      </form>

      <ul className="mt-8 w-full max-w-md space-y-2">
        {bookmarks.map((bm) => (
          <li key={bm.id} className="border p-3 rounded">
            <a href={bm.url} target="_blank" rel="noopener noreferrer" className="text-blue-600">
              {bm.url}
            </a>
            {bm.note && <p className="text-gray-600">{bm.note}</p>}
            <p className="text-xs text-gray-400">Saved on {new Date(bm.created_at).toLocaleString()}</p>
          </li>
        ))}
      </ul>
    </main>
  );
}

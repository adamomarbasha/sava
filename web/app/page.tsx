"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useAuth } from "./contexts/AuthContext";
import { useRouter } from "next/navigation";
import { Button, Card, CardContent, Input, Label, Spinner, Badge } from "./components/UI";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

interface Bookmark {
  id: number;
  url: string;
  title?: string;
  note?: string;
  platform?: string;
  created_at: string;
}

function extractYouTubeId(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname.includes("youtu.be")) return u.pathname.slice(1);
    if (u.hostname.includes("youtube.com")) return u.searchParams.get("v");
  } catch {}
  return null;
}

function getThumbnail(url: string, platform?: string): string | null {
  const ytId = extractYouTubeId(url);
  if (platform === "youtube" && ytId) return `https://img.youtube.com/vi/${ytId}/maxresdefault.jpg`;
  try {
    const u = new URL(url);
    return `https://www.google.com/s2/favicons?sz=128&domain=${u.hostname}`;
  } catch {
    return null;
  }
}

function PlatformIcon({ platform, size = "w-5 h-5" }: { platform: string; size?: string }) {
  const icons: Record<string, React.ReactElement> = {
    youtube: (
      <svg className={`${size} text-red-600`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
      </svg>
    ),
    instagram: (
      <svg className={`${size} text-pink-600`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>
      </svg>
    ),
    tiktok: (
      <svg className={`${size} text-black`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64 2.93 2.93 0 0 1 .88.13V9.4a6.84 6.84 0 0 0-1-.05A6.33 6.33 0 0 0 5 20.1a6.34 6.34 0 0 0 10.86-4.43v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-1-.1z"/>
      </svg>
    ),
    twitter: (
      <svg className={`${size} text-black`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
      </svg>
    ),
    web: (
      <svg className={`${size} text-gray-600`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.94-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
      </svg>
    ),
  };
  
  return icons[platform] || icons.web;
}

function Navbar() {
  const { user, logout } = useAuth();
  return (
    <nav className="navbar">
      <div className="max-w-7xl mx-auto px-6 py-3 w-full flex justify-between items-center">
        <div className="flex items-center gap-4">
          <img 
            src="/savaFav.png" 
            alt="Sava Logo" 
            className="h-16 w-16 object-contain"
          />
          <span className="logo-font text-2xl text-black">Sava</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden sm:block text-sm text-black/90 font-medium px-4 py-2 rounded-full bg-black/10 backdrop-blur-sm">
            {user?.email}
          </span>
          <button 
            onClick={logout} 
            className="px-4 py-2 rounded-full bg-black/10 backdrop-blur-sm text-black text-sm font-medium hover:bg-black/20 transition-all duration-200 border border-white/20 hover:scale-105"
          >
            Logout
          </button>
        </div>
      </div>
    </nav>
  );
}

function PlatformFilterButton({ 
  platform, 
  label, 
  isActive, 
  onClick, 
  count 
}: { 
  platform: string; 
  label: string; 
  isActive: boolean; 
  onClick: () => void; 
  count: number;
}) {
  const getHoverColor = (platform: string) => {
    switch (platform) {
      case 'youtube': return 'hover:bg-red-50 hover:border-red-200 hover:text-red-600';
      case 'instagram': return 'hover:bg-pink-50 hover:border-pink-200 hover:text-pink-600';
      case 'tiktok': return 'hover:bg-gray-50 hover:border-gray-300 hover:text-black';
      case 'twitter': return 'hover:bg-blue-50 hover:border-blue-200 hover:text-blue-600';
      default: return 'hover:bg-gray-50 hover:border-gray-300 hover:text-gray-700';
    }
  };

  const getActiveColor = (platform: string) => {
    switch (platform) {
      case 'youtube': return 'bg-red-100 border-red-300 text-red-700';
      case 'instagram': return 'bg-pink-100 border-pink-300 text-pink-700';
      case 'tiktok': return 'bg-gray-100 border-gray-400 text-black';
      case 'twitter': return 'bg-blue-100 border-blue-300 text-blue-700';
      default: return 'bg-gray-100 border-gray-400 text-gray-700';
    }
  };

  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-full border-2 text-sm font-medium transition-all duration-200 transform hover:scale-105 ${
        isActive 
          ? getActiveColor(platform)
          : `bg-white border-gray-200 text-gray-600 ${getHoverColor(platform)}`
      }`}
    >
      <PlatformIcon platform={platform} size="w-4 h-4" />
      <span>{label}</span>
      <span className="text-xs opacity-70 font-normal">({count})</span>
    </button>
  );
}

const PLATFORM_META: Record<string, { label: string; color: Parameters<typeof Badge>[0]["color"] }> = {
  youtube: { label: "YouTube", color: "rose" },
  instagram: { label: "Instagram", color: "purple" },
  tiktok: { label: "TikTok", color: "amber" },
  twitter: { label: "Twitter / X", color: "blue" },
  web: { label: "Other", color: "slate" },
};

export default function Home() {
  const { user, token, loading } = useAuth();
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [bookmarkLoading, setBookmarkLoading] = useState(false);
  const [fetchingBookmarks, setFetchingBookmarks] = useState(false);
  const [platformFilter, setPlatformFilter] = useState<string>("");
  const [search, setSearch] = useState<string>("");

  useEffect(() => {
    if (typeof window !== 'undefined') {
      (window as any).debugBookmarks = () => {
        console.log('=== BOOKMARK DEBUG INFO ===');
        console.log('Total bookmarks:', bookmarks.length);
        console.log('Current platform filter:', platformFilter);
        console.log('Current search:', search);
        console.log('Bookmarks by platform:');
        const byPlatform = bookmarks.reduce((acc: any, bookmark) => {
          acc[bookmark.platform || 'undefined'] = (acc[bookmark.platform || 'undefined'] || 0) + 1;
          return acc;
        }, {});
        console.table(byPlatform);
        console.log('Sample bookmarks:', bookmarks.slice(0, 3).map(b => ({
          id: b.id,
          url: b.url.substring(0, 50) + '...',
          platform: b.platform,
          title: b.title
        })));
        const filtered = bookmarks.filter(bookmark => {
          const matchesSearch = !search || 
            bookmark.title?.toLowerCase().includes(search.toLowerCase()) ||
            bookmark.url.toLowerCase().includes(search.toLowerCase()) ||
            bookmark.note?.toLowerCase().includes(search.toLowerCase());
          const matchesPlatform = !platformFilter || bookmark.platform === platformFilter;
          return matchesSearch && matchesPlatform;
        });
        console.log('Filtered results:', filtered.length);
        return { bookmarks, platformFilter, search, filtered, byPlatform };
      };
    }
  }, [bookmarks, platformFilter, search]);

  useEffect(() => { if (!loading && !user) router.push("/auth/login"); }, [user, loading, router]);
  
  const fetchBookmarks = useCallback(async () => {
    if (!token) return;
    
    setFetchingBookmarks(true);
    try {
      const res = await fetch(`${API_BASE}/bookmarks`, { 
        headers: { Authorization: `Bearer ${token}` } 
      });
      if (res.ok) {
        const fetchedBookmarks = await res.json();
        setBookmarks(fetchedBookmarks);
      } else {
        console.error("Failed to fetch bookmarks:", res.status, res.statusText);
      }
    } catch (error) {
      console.error("Failed to fetch bookmarks:", error);
    } finally {
      setFetchingBookmarks(false);
    }
  }, [token]);

  useEffect(() => { 
    if (token && !loading) {
      fetchBookmarks(); 
    }
  }, [token, loading, fetchBookmarks]);

  useEffect(() => {
    const nodes = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          obs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.1 });
    nodes.forEach((n) => obs.observe(n));
    return () => obs.disconnect();
  }, [bookmarks]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBookmarkLoading(true);
    try {
      const res = await fetch(`${API_BASE}/bookmarks`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ url, note }),
      });
      if (res.ok) {
        const newBookmark = await res.json();
        setBookmarks((prev) => [newBookmark, ...prev]);
        setUrl("");
        setNote("");
      }
    } catch (error) { 
      console.error("Error creating bookmark:", error); 
    } finally { 
      setBookmarkLoading(false); 
    }
  };

  const platformCounts = useMemo(() => {
    return {
      all: bookmarks.length,
      youtube: bookmarks.filter(b => b.platform === 'youtube').length,
      tiktok: bookmarks.filter(b => b.platform === 'tiktok').length,
      instagram: bookmarks.filter(b => b.platform === 'instagram').length,
      twitter: bookmarks.filter(b => b.platform === 'twitter').length,
      web: bookmarks.filter(b => b.platform === 'web').length,
    };
  }, [bookmarks]);

  const filtered = useMemo(() => {
    let result = [...bookmarks];
    
    if (search.trim()) {
      const searchLower = search.toLowerCase();
      result = result.filter(bookmark => 
        bookmark.title?.toLowerCase().includes(searchLower) ||
        bookmark.note?.toLowerCase().includes(searchLower) ||
        bookmark.url.toLowerCase().includes(searchLower)
      );
    }
    
    if (platformFilter) {
      result = result.filter(bookmark => bookmark.platform === platformFilter);
    }
    
    return result;
  }, [bookmarks, search, platformFilter]);

  if (loading) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center bg-white">
        <div className="inline-flex items-center gap-3 text-lg text-gray-600">
          <Spinner className="w-6 h-6 text-gray-800"/> 
          Loading...
        </div>
      </main>
    );
  }
  if (!user) return null;

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />

      
      <div className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
            
            
            <div className="flex flex-wrap items-center justify-center lg:justify-start gap-3">
              
              {(search || platformFilter) && (
                <button 
                  onClick={() => {
                    setSearch("");
                    setPlatformFilter("");
                  }}
                  className="text-sm text-gray-500 hover:text-red-600 transition-colors px-3 py-1 rounded-full hover:bg-red-50"
                >
                  Clear Filters ✕
                </button>
              )}
              
              
              {[
                { key: "", label: "All", platform: "web", countKey: "all" },
                { key: "youtube", label: "YouTube", platform: "youtube", countKey: "youtube" },
                { key: "tiktok", label: "TikTok", platform: "tiktok", countKey: "tiktok" },
                { key: "instagram", label: "Instagram", platform: "instagram", countKey: "instagram" },
                { key: "twitter", label: "Twitter/X", platform: "twitter", countKey: "twitter" },
                { key: "web", label: "Other", platform: "web", countKey: "web" },
              ].map((p) => (
                <PlatformFilterButton
                  key={p.key || "all"}
                  platform={p.platform}
                  label={p.label}
                  isActive={platformFilter === p.key}
                  onClick={() => setPlatformFilter(p.key)}
                  count={platformCounts[p.countKey as keyof typeof platformCounts]}
                />
              ))}
            </div>

            
            <div className="flex justify-center lg:justify-end">
              <div className="relative">
                <input
                  className="search-bar"
                  placeholder="Search bookmarks..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <svg 
                  className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" 
                  fill="none" 
                  stroke="currentColor" 
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              </div>
            </div>
          </div>
        </div>
      </div>

      
      <div className="max-w-7xl mx-auto px-6 py-8">

        
        <div className="mb-12">
          <h2 className="text-2xl font-bold text-gray-900 mb-6 text-center">Add Bookmark</h2>
          <div className="max-w-4xl mx-auto">
            <Card className="shadow-lg border-0 bg-white">
              <CardContent className="p-8">
                <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_1fr_auto] md:items-end">
                  <div>
                    <Label htmlFor="url" className="text-gray-700 font-medium">URL</Label>
                    <Input 
                      id="url" 
                      type="url" 
                      value={url} 
                      onChange={(e) => setUrl(e.target.value)} 
                      placeholder="Paste YouTube, Instagram, TikTok, or any URL" 
                      required 
                      className="mt-1 h-12"
                    />
                  </div>
                  <div>
                    <Label htmlFor="note" className="text-gray-700 font-medium">Note (optional)</Label>
                    <Input 
                      id="note" 
                      type="text" 
                      value={note} 
                      onChange={(e) => setNote(e.target.value)} 
                      placeholder="Add a personal note" 
                      className="mt-1 h-12"
                    />
                  </div>
                  <div className="md:pb-[2px]">
                    <button 
                      type="submit" 
                      disabled={bookmarkLoading} 
                      className="btn-primary-clean px-8 py-3 disabled:opacity-50 w-full md:w-auto text-base hover:shadow-lg h-12"
                    >
                      {bookmarkLoading ? "Saving..." : "Save Bookmark"}
                    </button>
                  </div>
                </form>
              </CardContent>
            </Card>
          </div>
        </div>

        
        <div className="mb-8">
          <div className="flex items-center justify-between mb-8">
            <h2 className="text-3xl font-bold text-gray-900">Your Saved Links</h2>
            <div className="flex items-center gap-3">
              {(search || platformFilter) && (
                <span className="text-sm text-gray-500">
                  {search && `Search: "${search}"`}
                  {search && platformFilter && " • "}
                  {platformFilter && `Platform: ${platformFilter} ${platformCounts[platformFilter as keyof typeof platformCounts]} bookmarks`}
                </span>
              )}
              <span className="text-lg text-gray-600 font-medium bg-gray-100 px-3 py-1 rounded-full">
                {filtered.length} bookmark{filtered.length !== 1 ? 's' : ''}
              </span>
            </div>
          </div>

          
          <div 
            key={`${platformFilter}-${search}-${filtered.length}`} 
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6"
          >
            {filtered.map((bm) => {
              const thumb = getThumbnail(bm.url, bm.platform);
              
              return (
                <Card 
                  key={bm.id} 
                  className="bookmark-card opacity-0 translate-y-3 will-change-transform rounded-xl shadow-md hover:shadow-xl border border-gray-200 bg-white overflow-hidden transition-all duration-300 hover:scale-[1.02]" 
                  data-reveal
                >
                  <CardContent className="p-0">
                    <div className="relative">
                      
                      <div className="absolute top-3 right-3 z-10">
                        <div className="bg-white/95 backdrop-blur-sm rounded-full p-2 shadow-sm border border-gray-100">
                          <PlatformIcon platform={bm.platform || 'web'} size="w-4 h-4" />
                        </div>
                      </div>

                      
                      <a 
                        href={bm.url} 
                        target="_blank" 
                        rel="noopener noreferrer" 
                        className="block group"
                      >
                        {thumb ? (
                          <img 
                            src={thumb} 
                            alt="thumbnail" 
                            className="w-full h-40 object-cover group-hover:scale-105 transition-transform duration-300" 
                          />
                        ) : (
                          <div className="w-full h-40 bg-gradient-to-br from-gray-100 to-gray-200 grid place-items-center text-3xl">
                            🔗
                          </div>
                        )}
                      </a>

                      
                      <div className="p-4">
                        
                        <a 
                          href={bm.url} 
                          target="_blank" 
                          rel="noopener noreferrer" 
                          className="block text-gray-900 hover:text-blue-600 font-bold text-base mb-2 leading-tight transition-colors duration-200 line-clamp-2"
                        >
                          {bm.title || bm.note || new URL(bm.url).hostname}
                        </a>
                        
                        
                        {bm.note && bm.title && (
                          <p className="text-gray-600 text-sm mb-3 leading-relaxed line-clamp-2">
                            {bm.note}
                          </p>
                        )}
                        
                        
                        <div className="flex items-center justify-between text-xs pt-2 border-t border-gray-100">
                          <span className="text-gray-400">
                            {new Date(bm.created_at).toLocaleDateString()}
                          </span>
                          <a 
                            href={bm.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-500 hover:text-blue-600 font-medium transition-colors duration-200 flex items-center gap-1"
                          >
                            Visit 
                            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                            </svg>
                          </a>
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}

            {filtered.length === 0 && !fetchingBookmarks && (
              <div className="text-center text-gray-500 py-20 md:col-span-2 lg:col-span-3 xl:col-span-4">
                <h3 className="text-xl font-semibold mb-2">No bookmarks found</h3>
                <p className="text-gray-400">
                  {search || platformFilter 
                    ? "Try adjusting your search or filter" 
                    : "Start by adding your first bookmark above"
                  }
                </p>
              </div>
            )}

            {fetchingBookmarks && (
              <div className="text-center text-gray-500 py-20 md:col-span-2 lg:col-span-3 xl:col-span-4">
                <Spinner />
                <h3 className="text-xl font-semibold mb-2 mt-4">Loading your bookmarks...</h3>
                <p className="text-gray-400">Please wait while we fetch your saved links</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

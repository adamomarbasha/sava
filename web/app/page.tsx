"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useAuth } from "./contexts/AuthContext";
import { useRouter } from "next/navigation";
import { Button, Card, CardContent, Input, Label, Spinner, Badge, Alert } from "./components/UI";
import { platform } from "os";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

interface Bookmark {
  id: number;
  url: string;
  title?: string;
  note?: string;
  platform?: string;
  author?: string;
  thumbnail_url?: string;
  published_at?: string;
  created_at: string;
  meta?: {
    video_id?: string;
    channel_id?: string;
    duration_seconds?: number;
    view_count?: number;
    like_count?: number;
    tags?: string[];
  };
}

function extractYouTubeId(url: string): string | null {
  try {
    const u = new URL(url);
    
    if (u.hostname.includes("youtu.be")) {
      return u.pathname.slice(1).split('?')[0];
    }
    
    if (u.hostname.includes("youtube.com")) {
      const videoId = u.searchParams.get("v");
      if (videoId) {
        return videoId.split('&')[0];
      }
    }
  } catch {}
  return null;
}

function normalizePlatform(platform?: string): string {
  const value = (platform || "").toLowerCase();
  if (!value || ["youtube", "tiktok", "instagram", "twitter", "linkedin", "reddit", "pinterest", "snapchat", "facebook", "web"].indexOf(value) === -1) {
    return "web";
  }
  return value;
}

function getYouTubeThumbnails(videoId: string): string[] {
  return [
    `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`,
    `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
    `https://img.youtube.com/vi/${videoId}/mqdefault.jpg`,
    `https://img.youtube.com/vi/${videoId}/default.jpg`
  ];
}

function SmartYouTubeThumbnail({ 
  videoId, 
  alt = "thumbnail", 
  className = "" 
}: { 
  videoId: string; 
  alt?: string; 
  className?: string; 
}) {
  const [imgSrc, setImgSrc] = useState(() => `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`);
  const [hasError, setHasError] = useState(false);
  
  useEffect(() => {
    setImgSrc(`https://img.youtube.com/vi/${videoId}/hqdefault.jpg`);
    setHasError(false);
  }, [videoId]);
  
  const handleImageError = () => {
    console.log(`Thumbnail failed for ${videoId}: ${imgSrc}`);
    
    if (imgSrc.includes('hqdefault.jpg')) {
      setImgSrc(`https://img.youtube.com/vi/${videoId}/mqdefault.jpg`);
    } else if (imgSrc.includes('mqdefault.jpg')) {
      setImgSrc(`https://img.youtube.com/vi/${videoId}/default.jpg`);
    } else {
      console.log(`All thumbnails failed for ${videoId}, showing placeholder`);
      setHasError(true);
    }
  };
  
  const handleImageLoad = () => {
    console.log(`Thumbnail loaded successfully for ${videoId}: ${imgSrc}`);
    setHasError(false);
  };
  
  if (hasError) {
    return (
      <div className={`w-full h-full flex items-center justify-center bg-gray-100 ${className}`}>
        <PlatformIcon platform="youtube" size="w-12 h-12" />
      </div>
    );
  }
  
  return (
    <img 
      src={imgSrc}
      alt={alt}
      className={className}
      onError={handleImageError}
      onLoad={handleImageLoad}
    />
  );
}

function getThumbnail(url: string, platform?: string, bookmark?: any): string | null {
  const ytId = extractYouTubeId(url);
  if (platform === "youtube" && ytId) {
    return `https://img.youtube.com/vi/${ytId}/maxresdefault.jpg`;
  }
  
  // Debug logging
  console.log({
    url: url.substring(0, 50) + '...',
    platform,
    hasBookmark: !!bookmark,
    thumbnail_url: bookmark?.thumbnail_url
  });
  
  if (bookmark?.thumbnail_url) {
    return bookmark.thumbnail_url;
  }
  
  return null;
}

function getVideoAspectRatio(platform: string, bookmark?: any): string {
  if (platform === "tiktok") {
    return "aspect-[3/4] max-w-[150px] mx-auto";
  }
  return "aspect-video";
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
    linkedin: (
      <svg className={`${size} text-blue-700`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
      </svg>
    ),
    reddit: (
      <svg className={`${size} text-orange-600`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M12 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0zm5.01 4.744c.688 0 1.25.561 1.25 1.249a1.25 1.25 0 0 1-2.498.056l-2.597-.547-.8 3.747c1.824.07 3.48.632 4.674 1.488.308-.309.73-.491 1.207-.491.968 0 1.754.786 1.754 1.754 0 .716-.435 1.333-1.01 1.614a3.111 3.111 0 0 1 .042.52c0 2.694-3.13 4.87-7.004 4.87-3.874 0-7.004-2.176-7.004-4.87 0-.183.015-.366.043-.534A1.748 1.748 0 0 1 4.028 12c0-.968.786-1.754 1.754-1.754.463 0 .898.196 1.207.49 1.207-.883 2.878-1.43 4.744-1.487l.885-4.182a.342.342 0 0 1 .14-.197.35.35 0 0 1 .238-.042l2.906.617a1.214 1.214 0 0 1 1.108-.701zM9.25 12C8.561 12 8 12.562 8 13.25c0 .687.561 1.248 1.25 1.248.687 0 1.248-.561 1.248-1.249 0-.688-.561-1.249-1.249-1.249zm5.5 0c-.687 0-1.248.561-1.248 1.25 0 .687.561 1.248 1.249 1.248.688 0 1.249-.561 1.249-1.249 0-.687-.562-1.249-1.25-1.249zm-5.466 3.99a.327.327 0 0 0-.231.094.33.33 0 0 0 0 .463c.842.842 2.484.913 2.961.913.477 0 2.105-.056 2.961-.913a.361.361 0 0 0 .029-.463.33.33 0 0 0-.464 0c-.547.533-1.684.73-2.512.73-.828 0-1.979-.196-2.512-.73a.326.326 0 0 0-.232-.095z"/>
      </svg>
    ),
    pinterest: (
      <svg className={`${size} text-red-600`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M12.017 0C5.396 0 .029 5.367.029 11.987c0 5.079 3.158 9.417 7.618 11.174-.105-.949-.199-2.403.041-3.439.219-.937 1.406-5.957 1.406-5.957s-.359-.72-.359-1.781c0-1.663.967-2.911 2.168-2.911 1.024 0 1.518.769 1.518 1.688 0 1.029-.653 2.567-.992 3.992-.285 1.193.6 2.165 1.775 2.165 2.128 0 3.768-2.245 3.768-5.487 0-2.861-2.063-4.869-5.008-4.869-3.41 0-5.409 2.562-5.409 5.199 0 1.033.394 2.143.889 2.741.097.118.112.221.083.342-.09.377-.293 1.199-.334 1.363-.053.225-.172.271-.402.165-1.495-.69-2.433-2.878-2.433-4.646 0-3.776 2.748-7.252 7.92-7.252 4.158 0 7.392 2.967 7.392 6.923 0 4.135-2.607 7.462-6.233 7.462-1.214 0-2.357-.629-2.748-1.378 0 0-.599 2.282-.744 2.84-.282 1.084-1.064 2.456-1.549 3.235C9.584 23.815 10.77 24.001 12.017 24.001c6.624 0 11.99-5.367 11.99-11.988C24.007 5.367 18.641.001 12.017.001z"/>
      </svg>
    ),
    snapchat: (
      <svg className={`${size} text-yellow-400`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M12.017 0C5.396 0 .029 5.367.029 11.987c0 5.079 3.158 9.417 7.618 11.174-.105-.949-.199-2.403.041-3.439.219-.937 1.406-5.957 1.406-5.957s-.359-.72-.359-1.781c0-1.663.967-2.911 2.168-2.911 1.024 0 1.518.769 1.518 1.688 0 1.029-.653 2.567-.992 3.992-.285 1.193.6 2.165 1.775 2.165 2.128 0 3.768-2.245 3.768-5.487 0-2.861-2.063-4.869-5.008-4.869-3.41 0-5.409 2.562-5.409 5.199 0 1.033.394 2.143.889 2.741.097.118.112.221.083.342-.09.377-.293 1.199-.334 1.363-.053.225-.172.271-.402.165-1.495-.69-2.433-2.878-2.433-4.646 0-3.776 2.748-7.252 7.92-7.252 4.158 0 7.392 2.967 7.392 6.923 0 4.135-2.607 7.462-6.233 7.462-1.214 0-2.357-.629-2.748-1.378 0 0-.599 2.282-.744 2.84-.282 1.084-1.064 2.456-1.549 3.235C9.584 23.815 10.77 24.001 12.017 24.001c6.624 0 11.99-5.367 11.99-11.988C24.007 5.367 18.641.001 12.017.001z"/>
      </svg>
    ),
    facebook: (
      <svg className={`${size} text-blue-600`} fill="currentColor" viewBox="0 0 24 24">
        <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
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
      case 'linkedin': return 'hover:bg-blue-50 hover:border-blue-200 hover:text-blue-700';
      case 'reddit': return 'hover:bg-orange-50 hover:border-orange-200 hover:text-orange-600';
      case 'pinterest': return 'hover:bg-red-50 hover:border-red-200 hover:text-red-600';
      case 'snapchat': return 'hover:bg-yellow-50 hover:border-yellow-200 hover:text-yellow-600';
      case 'facebook': return 'hover:bg-blue-50 hover:border-blue-200 hover:text-blue-600';
      default: return 'hover:bg-gray-50 hover:border-gray-300 hover:text-gray-700';
    }
  };

  const getActiveColor = (platform: string) => {
    switch (platform) {
      case 'youtube': return 'bg-red-100 border-red-300 text-red-700';
      case 'instagram': return 'bg-pink-100 border-pink-300 text-pink-700';
      case 'tiktok': return 'bg-gray-100 border-gray-400 text-black';
      case 'twitter': return 'bg-blue-100 border-blue-300 text-blue-700';
      case 'linkedin': return 'bg-blue-100 border-blue-300 text-blue-800';
      case 'reddit': return 'bg-orange-100 border-orange-300 text-orange-700';
      case 'pinterest': return 'bg-red-100 border-red-300 text-red-700';
      case 'snapchat': return 'bg-yellow-100 border-yellow-300 text-yellow-700';
      case 'facebook': return 'bg-blue-100 border-blue-300 text-blue-700';
      default: return 'bg-gray-100 border-gray-400 text-gray-700';
    }
  };

  return (
    <button
      type="button"
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
  linkedin: { label: "LinkedIn", color: "blue" },
  reddit: { label: "Reddit", color: "amber" },
  pinterest: { label: "Pinterest", color: "rose" },
  snapchat: { label: "Snapchat", color: "amber" },
  facebook: { label: "Facebook", color: "blue" },
  web: { label: "Other", color: "slate" },
};

// Add dropdown component
function DropdownMenu({ 
  onEdit, 
  onDelete 
}: { 
  onEdit: () => void; 
  onDelete: () => void; 
}) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="p-1 rounded-full hover:bg-gray-100 transition-all duration-200 flex items-center justify-center"
        aria-label="More options"
      >
        <svg className="w-4 h-4 text-gray-500 hover:text-gray-700" fill="currentColor" viewBox="0 0 24 24">
          <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 w-36 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-30">
          <button
            onClick={() => {
              onEdit();
              setIsOpen(false);
            }}
            className="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.5L15.232 5.232z" />
            </svg>
            Edit Note
          </button>
          <button
            onClick={() => {
              onDelete();
              setIsOpen(false);
            }}
            className="w-full px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

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
  const [bookmarkError, setBookmarkError] = useState<string>("");
  const [editingBookmark, setEditingBookmark] = useState<Bookmark | null>(null);
  const [editedNote, setEditedNote] = useState("");

  useEffect(() => {
    if (typeof window !== 'undefined') {
      (window as any).debugBookmarks = () => {
        console.log('=== BOOKMARK DEBUG INFO ===');
        console.log('Total bookmarks:', bookmarks.length);
        console.log('Current platform filter:', platformFilter);
        console.log('Current search:', search);
        console.log('Bookmarks by platform:');
        const byPlatform = bookmarks.reduce((acc: any, bookmark) => {
          const p = normalizePlatform(bookmark.platform);
          acc[p] = (acc[p] || 0) + 1;
          return acc;
        }, {});
        console.table(byPlatform);
        console.log('Sample bookmarks:', bookmarks.slice(0, 3).map(b => ({
          id: b.id,
          url: b.url.substring(0, 50) + '...',
          platform: b.platform,
          title: b.title,
          thumbnail_url: b.thumbnail_url
        })));
        const filtered = bookmarks.filter(bookmark => {
          const matchesSearch = !search || 
            bookmark.title?.toLowerCase().includes(search.toLowerCase()) ||
            bookmark.url.toLowerCase().includes(search.toLowerCase()) ||
            bookmark.note?.toLowerCase().includes(search.toLowerCase());
          const matchesPlatform = !platformFilter || normalizePlatform(bookmark.platform) === platformFilter;
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
      const res = await fetch(`${API_BASE}/api/bookmarks`, { 
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
  }, [bookmarks, platformFilter, search]);

  const validateUrl = (url: string): string | null => {
    if (!url.trim()) {
      return "Please enter a URL";
    }
    
    let normalizedUrl = url.trim();
    if (!normalizedUrl.match(/^https?:\/\//)) {
      normalizedUrl = `https://${normalizedUrl}`;
    }
    
    try {
      new URL(normalizedUrl);
      return null;
    } catch {
      return "Please enter a valid URL";
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBookmarkError("");
    
    const urlError = validateUrl(url);
    if (urlError) {
      setBookmarkError(urlError);
      return;
    }
    
    setBookmarkLoading(true);
    
    let normalizedUrl = url.trim();
    if (normalizedUrl && !normalizedUrl.match(/^https?:\/\//)) {
      normalizedUrl = `https://${normalizedUrl}`;
    }
    
    try {
      const timeoutPromise = new Promise((_, reject) => 
        setTimeout(() => reject(new Error('Request timed out after 60 seconds')), 60000)
      );
      
      const fetchPromise = fetch(`${API_BASE}/bookmarks`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ url: normalizedUrl, note }),
      });
      
      const res = await Promise.race([fetchPromise, timeoutPromise]) as Response;
      
      if (res.ok) {
        const newBookmark = await res.json();
        setBookmarks((prev) => [newBookmark, ...prev]);
        setUrl("");
        setNote("");
        setBookmarkError("");
      } else {
        const errorData = await res.json().catch(() => ({}));
        const errorMessage = errorData.detail || `Failed to create bookmark (${res.status})`;
        console.error("Failed to create bookmark:", errorMessage);
        
        if (res.status === 409 && errorMessage.toLowerCase().includes('already')) {
          setBookmarkError(`Duplicate Link: This URL has already been bookmarked.`);
        } else if (res.status === 400) {
          setBookmarkError(`Invalid URL: Please check the URL and try again.`);
        } else if (res.status === 401) {
          setBookmarkError(`Authentication Error: Please log in again.`);
        } else if (res.status >= 500) {
          setBookmarkError(`Server Error: Our servers are having issues. Please try again later.`);
        } else {
          setBookmarkError(`Error: ${errorMessage}`);
        }
      }
    } catch (error) { 
      console.error("Error creating bookmark:", error);
      if (error instanceof Error && error.message.includes('timed out')) {
        setBookmarkError("Request timed out after 60 seconds. YouTube extraction can be slow - please try again or use a different video.");
      } else {
        setBookmarkError("Network Error: Failed to create bookmark. Please check your connection and try again.");
      }
    } finally { 
      setBookmarkLoading(false); 
    }
  };

  const handleDelete = async (id: number) => {
    try {
      const res = await fetch(`${API_BASE}/api/bookmarks/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        setBookmarks(bookmarks.filter(b => b.id !== id));
      } else {
        console.error("Failed to delete bookmark");
        setBookmarkError("Failed to delete bookmark. Please try again.");
      }
    } catch (error) {
      console.error("Failed to delete bookmark:", error);
      setBookmarkError("Network error while deleting. Please try again.");
    }
  };

  const handleEdit = (bookmark: Bookmark) => {
    setEditingBookmark(bookmark);
    setEditedNote(bookmark.note || "");
  };

  const handleUpdateNote = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingBookmark) return;

    try {
      const res = await fetch(`${API_BASE}/api/bookmarks/${editingBookmark.id}`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ note: editedNote }),
      });

      if (res.ok) {
        const updatedBookmark = await res.json();
        setBookmarks(bookmarks.map(b => b.id === updatedBookmark.id ? updatedBookmark : b));
        setEditingBookmark(null);
      } else {
        console.error("Failed to update note");
      }
    } catch (error) {
      console.error("Failed to update note:", error);
    }
  };

  const platformCounts = useMemo(() => {
    return {
      all: bookmarks.length,
      youtube: bookmarks.filter(b => normalizePlatform(b.platform) === 'youtube').length,
      tiktok: bookmarks.filter(b => normalizePlatform(b.platform) === 'tiktok').length,
      instagram: bookmarks.filter(b => normalizePlatform(b.platform) === 'instagram').length,
      twitter: bookmarks.filter(b => normalizePlatform(b.platform) === 'twitter').length,
      web: bookmarks.filter(b => normalizePlatform(b.platform) === 'web').length,
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
      result = result.filter(bookmark => normalizePlatform(bookmark.platform) === platformFilter);
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
              <CardContent className="p-8 pt-4">
                {bookmarkError && (
                  <div className="mb-6">
                    <Alert variant="error">{bookmarkError}</Alert>
                  </div>
                )}
                <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_1fr_auto] md:items-end">
                  <div>
                    <Label htmlFor="url" className="text-gray-700 font-medium">URL</Label>
                    <Input 
                      id="url" 
                      type="text" 
                      value={url} 
                      onChange={(e) => {
                        setUrl(e.target.value);
                        if (bookmarkError) setBookmarkError("");
                      }} 
                      placeholder="Add any Link!" 
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
                      {bookmarkLoading ? "Extracting metadata..." : "Save Bookmark"}
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
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8"
          >
            {filtered.map((bm) => {
              const thumb = getThumbnail(bm.url, bm.platform, bm);
              
              return (
                <Card 
                  key={`bookmark-${bm.id}-${bm.url}`} 
                  className="bookmark-card opacity-0 translate-y-3 will-change-transform rounded-xl shadow-md hover:shadow-xl border border-gray-200 bg-white overflow-hidden transition-all duration-300 hover:scale-105 h-[330px] flex flex-col"
                  data-reveal
                >
                  <CardContent className="p-0 flex flex-col h-full relative">
                    <div className="absolute top-2 left-2 z-20 flex gap-2">
                      <DropdownMenu 
                        onEdit={() => handleEdit(bm)} 
                        onDelete={() => handleDelete(bm.id)} 
                      />
                    </div>
                    <div className="absolute top-2 right-2 z-10">
                      <div className="bg-white/90 backdrop-blur-sm rounded-lg p-1.5 shadow-lg border border-white/20">
                        <PlatformIcon platform={bm.platform || 'web'} size="w-3.5 h-3.5" />
                      </div>
                    </div>
                    
                    <div className="flex flex-col h-full">

                      <div className="p-4 pb-2">
                        <a 
                          href={bm.url} 
                          target="_blank" 
                          rel="noopener noreferrer" 
                          className="block text-gray-900 hover:text-blue-600 font-semibold text-base leading-tight transition-colors duration-200 line-clamp-2"
                        >
                          {bm.title || bm.note || new URL(bm.url).hostname}
                        </a>
                      </div>

                      <div className="flex-1 px-4 flex items-center justify-center">
                        <a 
                          href={bm.url} 
                          target="_blank" 
                          rel="noopener noreferrer" 
                          className="block group"
                        >
                          <div className={`${getVideoAspectRatio(bm.platform || 'web', bm)} overflow-hidden rounded-md bg-gray-100`}>
                            {bm.platform === "youtube" ? (
                              (() => {
                                const ytId = extractYouTubeId(bm.url);
                                return ytId ? (
                                  <SmartYouTubeThumbnail 
                                    key={ytId}
                                    videoId={ytId}
                                    alt="YouTube thumbnail"
                                    className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                                  />
                                ) : (
                                  <div className="w-full h-full flex items-center justify-center">
                                    <PlatformIcon platform="youtube" size="w-12 h-12" />
                                  </div>
                                );
                              })()
                            ) : thumb ? (
                              <>
                                <img 
                                  src={thumb} 
                                  alt="thumbnail" 
                                  className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" 
                                  onError={(e) => { 
                                    e.currentTarget.style.display = "none"; 
                                    const fallback = e.currentTarget.nextElementSibling as HTMLElement; 
                                    if (fallback) fallback.style.display = "flex"; 
                                  }}                                />
                                <div className="w-full h-full items-center justify-center bg-gray-100" style={{display: "none"}}>
                                  <PlatformIcon platform={bm.platform || "web"} size="w-12 h-12" />
                                </div>
                              </>
                            ) : (
                              <div className="w-full h-full flex items-center justify-center">
                                <PlatformIcon platform={bm.platform || 'web'} size="w-12 h-12" />
                              </div>
                            )}
                          </div>
                        </a>
                      </div>

                      <div className="p-4 pt-3 mt-auto">
                        {bm.note && (
                          <p className="text-gray-600 text-sm mb-2 line-clamp-2 italic">
                            "{bm.note}"
                          </p>
                        )}
                        <span className="text-gray-500 text-sm font-medium">
                          {new Date(bm.created_at).toLocaleDateString()}
                        </span>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}

            {filtered.length === 0 && !fetchingBookmarks && (
              <div className="text-center text-gray-500 py-20 md:col-span-2 lg:col-span-3">
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
              <div className="text-center text-gray-500 py-20 md:col-span-2 lg:col-span-3">
                <Spinner />
                <h3 className="text-xl font-semibold mb-2 mt-4">Loading your bookmarks...</h3>
                <p className="text-gray-400">Please wait while we fetch your saved links</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {editingBookmark && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <Card className="w-full max-w-lg">
            <CardContent className="p-6">
              <h3 className="text-lg font-semibold mb-1 pt-2">Edit Note</h3>
              <form onSubmit={handleUpdateNote}>
                <textarea
                  value={editedNote}
                  onChange={(e) => setEditedNote(e.target.value)}
                  className="w-full h-32 p-2 border rounded-md"
                  placeholder="Your note..."
                />
                <div className="flex justify-end gap-2 mt-4">
                  <Button type="button" variant="secondary" onClick={() => setEditingBookmark(null)}>
                    Cancel
                  </Button>
                  <Button type="submit">Save</Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

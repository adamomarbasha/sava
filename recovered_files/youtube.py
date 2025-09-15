@import "tailwindcss";

:root {
  /* Light (white) palette */
  --background: 
  --surface: 
  --foreground: 
  --muted: 
  --muted-foreground: 
  --border: 

  /* Neutral dark accents for buttons/links */
  --brand: 
  --brand-light: 
  --brand-dark: 

  --ring: var(--brand);
}

@theme inline {
  --color-background: var(--background);
  --color-surface: var(--surface);
  --color-foreground: var(--foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-border: var(--border);
  --color-brand: var(--brand);
  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);
}

/* Scroll reveal animation for cards */
[data-reveal] { 
  opacity: 0; 
  transform: translateY(20px); 
  transition: opacity 0.8s cubic-bezier(0.4, 0, 0.2, 1), transform 0.8s cubic-bezier(0.4, 0, 0.2, 1); 
}
[data-reveal].is-visible { 
  opacity: 1; 
  transform: translateY(0); 
}

html, body { height: 100%; }

body {
  background: var(--background);
  color: var(--foreground);
  font-family: var(--font-geist-sans), system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
}

.container-page {
  max-width: 1120px;
  margin-inline: auto;
  padding-inline: 1.5rem;
}

.card-elevated {
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 1rem;
  box-shadow: 0 10px 30px rgba(0,0,0,0.06);
}

.link { color: var(--brand); }

.navbar {
  position: sticky;
  top: 0;
  z-index: 50;

  background: linear-gradient(
    135deg,



  );
  color:

  padding: 0.5rem 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;

  border-bottom: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}



/* ===== SEARCH BAR ===== */
.search-bar {
  background: 
  border: 2px solid var(--border);
  border-radius: 0.75rem;
  padding: 0.875rem 1rem;
  color: var(--foreground);
  outline: none;
  font-size: 0.95rem;
  width: 300px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  transition: none;
}

.search-bar::placeholder { 
  color: 
}

/* No border/glow effects */
.search-bar:focus {
  border-color: var(--border);
  box-shadow: none;
}

/* ===== BUTTONS ===== */
.btn-primary-clean {
  background: 
  color: 
  border-radius: 0.75rem;
  border: 1px solid 
  font-weight: 600;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.btn-primary-clean:hover { 
  background:  
  transform: translateY(-1px);
  box-shadow: 0 8px 25px rgba(0,0,0,0.2);
}

.btn-primary-clean:active { 
  background:
  transform: translateY(0px);
}

/* ===== PLATFORM FILTER BUTTONS ===== */
.platform-filter-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 1rem;
  border-radius: 9999px;
  border: 2px solid 
  background: 
  font-size: 0.875rem;
  font-weight: 500;
  color: 
  cursor: pointer;
  transition: transform 0.2s ease;
}

.platform-filter-pill:hover {
  transform: scale(1.05);
  box-shadow: none;
}

/* ===== BOOKMARK CARDS ===== */
.bookmark-card {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.bookmark-card:hover {
  transform: translateY(-4px) scale(1.02);
  box-shadow: 0 20px 40px rgba(0,0,0,0.12);
}

/* Text clamping */
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ===== MISC ===== */
.platform-icon-container {
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.2);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.logo-font {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  font-weight: 700;
  letter-spacing: -0.025em;
  text-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

* {
  transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
}

/* Removed default focus outline */
button:focus-visible,
input:focus-visible {
  outline: none;
}

/* Responsive */
@media (max-width: 768px) {
  .search-bar {
    width: 100%;
    max-width: 280px;
  }
  
  .platform-filter {
    font-size: 0.875rem;
    padding: 0.5rem 0.75rem;
  }
}

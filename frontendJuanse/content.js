// Homepage editor support (see editor.html). Applies the text and fonts saved in the editor.
// Editable elements carry data-edit="key"; anything not edited keeps the text written in index.html.
// Needs lang.js (applyLanguage, currentLang) and config.js (API_BASE_URL) loaded first.

const CONTENT_STORAGE_KEY = 'dalbagni-content';

// Turns editor text into safe HTML: escapes everything, then allows **bold** and line breaks
function renderEditedText(text) {
    const escaped = text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    return escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
}

function applySiteContent(content) {
    const texts = content.texts || {};
    document.querySelectorAll('[data-edit]').forEach((el) => {
        const value = texts[el.dataset.edit] || {};
        for (const lang of ['en', 'es']) {
            // Remember the text written in index.html, so a text reset in the editor goes back to it
            const originalKey = `original${lang === 'en' ? 'En' : 'Es'}`;
            if (el.dataset[originalKey] === undefined) el.dataset[originalKey] = el.getAttribute(`data-${lang}`) || '';
            const edited = typeof value[lang] === 'string' ? renderEditedText(value[lang]) : el.dataset[originalKey];
            el.setAttribute(`data-${lang}`, edited);
        }
    });
    applyLanguage(currentLang);

    const families = [];
    for (const [role, font] of Object.entries(content.fonts || {})) {
        if (!font || !font.stack) continue;
        document.documentElement.style.setProperty(`--font-${role}`, font.stack);
        if (font.google) families.push(`family=${font.google}`);
    }
    // Load any Google Font the page doesn't already include
    const href = `https://fonts.googleapis.com/css2?${families.join('&')}&display=swap`;
    if (families.length && !document.querySelector(`link[href="${href}"]`)) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = href;
        document.head.append(link);
    }
}

async function loadSiteContent() {
    // Show the last saved version right away, then refresh it from the server
    try {
        const saved = JSON.parse(localStorage.getItem(CONTENT_STORAGE_KEY));
        if (saved) applySiteContent(saved);
    } catch {
        // Nothing saved yet, or storage blocked
    }

    try {
        const response = await fetch(`${API_BASE_URL}/api/content`);
        const content = await response.json();
        if (!response.ok || !content.success) return;
        applySiteContent(content);
        try {
            localStorage.setItem(CONTENT_STORAGE_KEY, JSON.stringify({ texts: content.texts, fonts: content.fonts }));
        } catch {
            // Storage blocked: the page still shows the latest content
        }
    } catch {
        // Server unreachable: the page keeps the text written in index.html
    }
}

// Wait for the whole page, since this script loads before parts of it (e.g. the footer)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadSiteContent);
} else {
    loadSiteContent();
}

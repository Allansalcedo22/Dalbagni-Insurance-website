// English / Spanish toggle shared by index.html and quote.html.
//
// Mark up translatable content with:
//   data-en="..." data-es="..."                         -> swaps the element's content
//   data-en-placeholder="..." data-es-placeholder="..."  -> swaps an input's placeholder
//   data-lang="en" / data-lang="es"                      -> shows only the block for the current language
//                                                          (for long passages with links, like the consent text)
// The toggle link needs id="lang-toggle". The chosen language is remembered across pages.

let currentLang = 'en';

function readSavedLang() {
    try {
        return localStorage.getItem('dalbagni-lang') === 'es' ? 'es' : 'en';
    } catch {
        return 'en';
    }
}

function applyLanguage(lang) {
    currentLang = lang;
    try {
        localStorage.setItem('dalbagni-lang', lang);
    } catch {
        // Storage blocked (private mode): the toggle still works on this page
    }

    document.documentElement.lang = lang;

    document.querySelectorAll('[data-en][data-es]').forEach((el) => {
        el.innerHTML = el.getAttribute(`data-${lang}`);
    });
    document.querySelectorAll('[data-en-placeholder][data-es-placeholder]').forEach((el) => {
        el.placeholder = el.getAttribute(`data-${lang}-placeholder`);
    });
    document.querySelectorAll('[data-lang]').forEach((el) => {
        el.hidden = el.dataset.lang !== lang;
    });

    const toggle = document.getElementById('lang-toggle');
    if (toggle) toggle.textContent = lang === 'en' ? 'Español' : 'English';

    document.dispatchEvent(new CustomEvent('languagechange', { detail: { lang } }));
}

document.getElementById('lang-toggle').addEventListener('click', (e) => {
    e.preventDefault();
    applyLanguage(currentLang === 'en' ? 'es' : 'en');
});

// Wait for the whole page, since this script may load before parts of it (e.g. the footer)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => applyLanguage(readSavedLang()));
} else {
    applyLanguage(readSavedLang());
}

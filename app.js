/**
 * Paper Pulse - Frontend Application
 *
 * Copyright (C) 2024-2026 Paper Pulse Contributors
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

// Global state
let allPapers = [];
let filteredPapers = [];
let currentPage = 1;
const papersPerPage = typeof CONFIG !== 'undefined' ? CONFIG.papersPerPage : 10;

// Load papers on page load
document.addEventListener('DOMContentLoaded', () => {
    loadPapers();
    setupEventListeners();
});

// Setup event listeners
function setupEventListeners() {
    const searchInput = document.getElementById('searchInput');
    const sourceFilter = document.getElementById('sourceFilter');
    const sortBy = document.getElementById('sortBy');
    const exportAllBtn = document.getElementById('exportAllBtn');

    searchInput.addEventListener('input', () => { currentPage = 1; filterAndDisplay(); });
    sourceFilter.addEventListener('change', () => { currentPage = 1; filterAndDisplay(); });
    sortBy.addEventListener('change', () => { currentPage = 1; filterAndDisplay(); });
    exportAllBtn.addEventListener('click', exportAllPapers);
}

// Toggle language for a specific paper card
function toggleLanguage(index, lang) {
    const summaryDiv = document.querySelector(`#paper-summary-${index}`);
    const zhLink = document.querySelector(`#lang-zh-${index}`);
    const enLink = document.querySelector(`#lang-en-${index}`);

    if (!summaryDiv || !zhLink || !enLink) return;

    const paper = filteredPapers[index];
    if (!paper) return;

    // Get the appropriate guide or faithful metadata translation.
    const summaryText = lang === 'zh'
        ? (paper.summary_zh || paper.abstract_zh || paper.summary || paper.abstract)
        : (paper.summary_en || paper.abstract || paper.summary);

    // Update content
    summaryDiv.innerHTML = renderMarkdown(summaryText);
    summaryDiv.dataset.lang = lang;

    // Update active states
    if (lang === 'zh') {
        zhLink.classList.add('active');
        enLink.classList.remove('active');
    } else {
        zhLink.classList.remove('active');
        enLink.classList.add('active');
    }
}

// Load papers from JSON
async function loadPapers() {
    try {
        const response = await fetch('data/papers.json');
        if (!response.ok) {
            throw new Error('Failed to load papers');
        }

        const data = await response.json();
        allPapers = data.papers || [];

        // Update last updated time
        if (data.last_updated) {
            const date = new Date(data.last_updated);
            document.getElementById('lastUpdated').textContent = date.toLocaleString();
        }

        filterAndDisplay();
    } catch (error) {
        console.error('Error loading papers:', error);
        document.getElementById('papersList').innerHTML =
            '<div class="no-results">Failed to load papers. Please try again later.</div>';
    }
}

// Filter and display papers
function filterAndDisplay() {
    const searchTerm = document.getElementById('searchInput').value.toLowerCase();
    const sourceFilter = document.getElementById('sourceFilter').value;
    const sortBy = document.getElementById('sortBy').value;

    // Filter papers
    filteredPapers = allPapers.filter(paper => {
        // Source filter
        if (sourceFilter !== 'all' && paper.source !== sourceFilter) {
            return false;
        }

        // Search filter
        if (searchTerm) {
            const searchableText = [
                paper.title,
                paper.title_zh || '',
                (paper.authors || []).join(' '),
                paper.summary || '',
                paper.summary_zh || '',
                paper.summary_en || '',
                paper.abstract || '',
                paper.abstract_zh || '',
                ...(paper.topics || []),
                ...(paper.keywords || [])
            ].join(' ').toLowerCase();

            if (!searchableText.includes(searchTerm)) {
                return false;
            }
        }

        return true;
    });

    // Sort papers
    sortPapers(filteredPapers, sortBy);

    // Display papers with pagination
    displayPapers(filteredPapers);
    updateStats(filteredPapers.length, allPapers.length);
    updatePagination(filteredPapers.length);
}

// Sort papers
function sortPapers(papers, sortBy) {
    switch (sortBy) {
        case 'date-desc':
            papers.sort((a, b) => b.published.localeCompare(a.published));
            break;
        case 'date-asc':
            papers.sort((a, b) => a.published.localeCompare(b.published));
            break;
        case 'title':
            papers.sort((a, b) => a.title.localeCompare(b.title));
            break;
    }
}

// Display papers
function displayPapers(papers) {
    const papersList = document.getElementById('papersList');

    if (papers.length === 0) {
        papersList.innerHTML = '<p class="no-results">没有符合条件的论文。</p>';
        return;
    }

    const startIdx = (currentPage - 1) * papersPerPage;
    const endIdx = startIdx + papersPerPage;
    const pagePapers = papers.slice(startIdx, endIdx);

    papersList.innerHTML = pagePapers.map((paper, index) => createPaperCard(paper, startIdx + index)).join('<hr>');

    // Add event listeners for BibTeX buttons
    pagePapers.forEach((paper, index) => {
        const btn = document.getElementById(`bibtex-${startIdx + index}`);
        if (btn) {
            btn.addEventListener('click', () => exportBibtex(paper));
        }
    });
}

// Render the small Markdown subset emitted by the summarizer. Escape first:
// paper abstracts and AI output are untrusted public input.
function renderMarkdown(text) {
    if (!text) return '';
    return escapeHtml(text)
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/^- (.+)$/gm, '• $1')
        .replace(/\n/g, '<br>');
}

// Create paper entry HTML
function createPaperCard(paper, index) {
    const paperAuthors = paper.authors || [];
    const authors = paperAuthors.slice(0, 5).join(', ') +
                   (paperAuthors.length > 5 ? ', et al.' : '');

    const keywords = [...(paper.topics || []), ...(paper.keywords || [])].slice(0, 8).join(', ');

    // Default to the Chinese guide or faithful metadata translation.
    const summaryText = paper.summary_zh || paper.abstract_zh || paper.summary || paper.abstract;
    const summaryHtml = renderMarkdown(summaryText);

    const chineseText = paper.summary_zh || paper.abstract_zh || '';
    const englishText = paper.summary_en || paper.abstract || '';
    const hasBilingual = Boolean(chineseText && englishText && chineseText !== englishText);
    const paperUrl = safeExternalUrl(paper.url, ['arxiv.org', 'eprint.iacr.org']);
    const pdfUrl = safeExternalUrl(paper.pdf_link, ['arxiv.org', 'eprint.iacr.org']);
    const displayTitle = paper.title_zh || paper.title;
    const titleLink = paperUrl
        ? `<a href="${escapeHtml(paperUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(displayTitle)}</a>`
        : escapeHtml(displayTitle);

    return `
        <div class="paper-entry">
            <div class="paper-title">${titleLink}</div>
            ${paper.title_zh ? `<div class="paper-original-title">英文原题：${escapeHtml(paper.title)}</div>` : ''}
            <div class="paper-meta">${escapeHtml(paper.source)} | ${escapeHtml(paper.published)} | ${relevanceLabel(paper)}</div>
            <div class="paper-authors">${escapeHtml(authors)}</div>
            ${hasBilingual ? `
            <div class="lang-toggle">
                <a class="active" id="lang-zh-${index}" onclick="toggleLanguage(${index}, 'zh')">中文</a>
                <a id="lang-en-${index}" onclick="toggleLanguage(${index}, 'en')">English</a>
            </div>
            ` : ''}
            <div class="paper-summary" id="paper-summary-${index}" data-lang="zh">
                ${summaryHtml}
            </div>
            ${keywords ? `<div class="paper-keywords">Keywords: ${escapeHtml(keywords)}</div>` : ''}
            <div class="paper-actions">
                ${paperUrl ? `<a href="${escapeHtml(paperUrl)}" target="_blank" rel="noopener noreferrer">查看论文</a>` : ''}
                ${pdfUrl ? `<a href="${escapeHtml(pdfUrl)}" target="_blank" rel="noopener noreferrer">PDF</a>` : ''}
                <button id="bibtex-${index}">BibTeX</button>
            </div>
        </div>
    `;
}

function relevanceLabel(paper) {
    if (paper.relevance_level === 'secure_inference') return '安全推理·全文精选';
    if (paper.relevance_level === 'related') return '兴趣相关·摘要导读';
    if (paper.source !== 'arXiv') return 'IACR 元数据';
    return paper.abstract_zh ? 'arXiv 宽召回·元数据翻译' : 'arXiv 宽召回·仅元数据';
}

// Update statistics
function updateStats(filtered, total) {
    const stats = document.getElementById('stats');
    const startIdx = (currentPage - 1) * papersPerPage + 1;
    const endIdx = Math.min(currentPage * papersPerPage, filtered);
    stats.textContent = filtered > 0
        ? `显示 ${startIdx}–${endIdx}，筛选结果 ${filtered} 篇（共 ${total} 篇）`
        : `无筛选结果（共 ${total} 篇）`;
}

// Update pagination controls
function updatePagination(totalFiltered) {
    const totalPages = Math.ceil(totalFiltered / papersPerPage);
    const paginationDiv = document.getElementById('pagination');

    if (totalPages <= 1) {
        paginationDiv.innerHTML = '';
        return;
    }

    let html = '<div class="pagination-controls">';

    if (currentPage > 1) {
        html += `<button onclick="changePage(${currentPage - 1})">Previous</button>`;
    }

    const maxButtons = 7;
    let startPage = Math.max(1, currentPage - Math.floor(maxButtons / 2));
    let endPage = Math.min(totalPages, startPage + maxButtons - 1);

    if (endPage - startPage < maxButtons - 1) {
        startPage = Math.max(1, endPage - maxButtons + 1);
    }

    if (startPage > 1) {
        html += `<button onclick="changePage(1)">1</button>`;
        if (startPage > 2) html += '<span>...</span>';
    }

    for (let i = startPage; i <= endPage; i++) {
        html += `<button class="${i === currentPage ? 'active' : ''}" onclick="changePage(${i})">${i}</button>`;
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) html += '<span>...</span>';
        html += `<button onclick="changePage(${totalPages})">${totalPages}</button>`;
    }

    if (currentPage < totalPages) {
        html += `<button onclick="changePage(${currentPage + 1})">Next</button>`;
    }

    html += '</div>';
    paginationDiv.innerHTML = html;
}

// Change page
function changePage(page) {
    currentPage = page;
    displayPapers(filteredPapers);
    updateStats(filteredPapers.length, allPapers.length);
    updatePagination(filteredPapers.length);
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Links originate in external feeds. Escaping attributes is not enough to
// reject executable schemes, so apply an explicit HTTPS/host allowlist.
function safeExternalUrl(value, allowedHosts) {
    if (!value) return '';
    try {
        const url = new URL(value);
        return url.protocol === 'https:' && allowedHosts.includes(url.hostname)
            ? url.href
            : '';
    } catch (_) {
        return '';
    }
}

// Generate BibTeX entry
function generateBibtex(paper) {
    const year = paper.published.split('-')[0];
    const authors = paper.authors.join(' and ');

    // Generate citation key
    const firstAuthor = paper.authors[0]?.split(' ').pop() || 'Unknown';
    const titleWords = paper.title.split(' ').slice(0, 2).join('');
    const key = `${firstAuthor}${year}${titleWords}`.replace(/[^a-zA-Z0-9]/g, '');

    // Determine entry type
    let entryType = 'article';
    let venue = '';

    if (paper.source === 'arXiv') {
        entryType = 'misc';
        venue = `  eprint = {${paper.arxiv_id}},\n  archivePrefix = {arXiv},`;
    } else if (paper.source === 'IACR') {
        entryType = 'misc';
        venue = `  howpublished = {Cryptology ePrint Archive, Paper ${paper.iacr_id}},\n  note = {\\url{${paper.url}}},`;
    }

    return `@${entryType}{${key},
  author = {${authors}},
  title = {${paper.title}},
  year = {${year}},
${venue}
  url = {${paper.url}}
}`;
}

// Export single paper as BibTeX
function exportBibtex(paper) {
    const bibtex = generateBibtex(paper);
    openTextInNewTab(bibtex);
}

// Export all papers as BibTeX
function exportAllPapers() {
    if (filteredPapers.length === 0) {
        alert('No papers to export');
        return;
    }

    const allBibtex = filteredPapers.map(p => generateBibtex(p)).join('\n\n');
    openTextInNewTab(allBibtex);
}

// Open text in a new browser tab
function openTextInNewTab(text) {
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank');
}

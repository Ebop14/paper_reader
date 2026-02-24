const UI = {
    $(id) { return document.getElementById(id); },

    renderPaperList(papers, activePaperId, onSelect) {
        const ul = this.$('paper-list');
        ul.innerHTML = '';
        for (const p of papers) {
            const li = document.createElement('li');
            li.className = p.id === activePaperId ? 'active' : '';
            li.innerHTML = `
                <span class="paper-name">${this.escHtml(p.filename)}</span>
                <span class="paper-meta">${p.num_pages} pages &middot; ${(p.total_chars / 1000).toFixed(1)}k chars</span>
            `;
            li.onclick = () => onSelect(p);
            ul.appendChild(li);
        }
    },

    renderSections(sections, activeChunkIndex) {
        const div = this.$('text-content');
        div.innerHTML = '';
        let currentTitle = null;
        for (const s of sections) {
            const sec = document.createElement('div');
            sec.className = 'section' + (s.chunk_index === activeChunkIndex ? ' active-chunk' : '');
            sec.dataset.chunk = s.chunk_index;

            // Only show title if it differs from the previous one
            if (s.title !== currentTitle) {
                sec.innerHTML = `<div class="section-title">${this.escHtml(s.title)}</div>`;
                currentTitle = s.title;
            }
            sec.innerHTML += `<div class="section-body">${this.escHtml(s.text)}</div>`;
            div.appendChild(sec);
        }
    },

    scrollToChunk(index) {
        const el = document.querySelector(`.section[data-chunk="${index}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },

    highlightChunk(index) {
        document.querySelectorAll('.section.active-chunk').forEach(el => el.classList.remove('active-chunk'));
        const el = document.querySelector(`.section[data-chunk="${index}"]`);
        if (el) el.classList.add('active-chunk');
    },

    showProgress(prefix, data) {
        const bar = this.$(`${prefix}-progress`);
        const fill = this.$(`${prefix}-progress-fill`);
        const label = this.$(`${prefix}-progress-label`);
        bar.style.display = 'block';
        fill.style.width = `${(data.progress * 100).toFixed(0)}%`;
        label.textContent = data.message || '';
        if (data.status === 'completed' || data.status === 'failed') {
            setTimeout(() => { bar.style.display = 'none'; }, 2000);
        }
    },

    renderMusicList(items, activeId, onSelect, onRemove) {
        const ul = this.$('music-list');
        ul.innerHTML = '';
        for (const m of items) {
            const li = document.createElement('li');
            li.className = m.id === activeId ? 'active' : '';
            li.innerHTML = `
                <span class="music-name">${this.escHtml(m.filename)}</span>
                <span class="music-remove" data-id="${m.id}">&times;</span>
            `;
            li.querySelector('.music-name').onclick = () => onSelect(m);
            li.querySelector('.music-remove').onclick = (e) => { e.stopPropagation(); onRemove(m.id); };
            ul.appendChild(li);
        }
    },

    setPlayerVisible(show) {
        this.$('player').style.display = show ? 'block' : 'none';
        this.$('export-section').style.display = show ? 'block' : 'none';
    },

    updatePlayButton(playing) {
        this.$('btn-play').innerHTML = playing ? '&#9646;&#9646;' : '&#9654;';
    },

    updateChunkIndicator(current, total) {
        this.$('chunk-current').textContent = current;
        this.$('chunk-total').textContent = total;
    },

    escHtml(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    },
};

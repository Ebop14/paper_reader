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

            if (s.title !== currentTitle) {
                sec.innerHTML = `<div class="section-title">${this.escHtml(s.title)}</div>`;
                currentTitle = s.title;
            }
            sec.innerHTML += `<div class="section-body">${this.escHtml(s.text)}</div>`;
            div.appendChild(sec);
        }
    },

    renderScript(script, activeSegmentIndex) {
        const div = this.$('text-content');
        div.innerHTML = '';

        if (!script || !script.segments || script.segments.length === 0) {
            div.innerHTML = '<p class="placeholder">No script generated yet</p>';
            return;
        }

        for (const seg of script.segments) {
            const card = document.createElement('div');
            card.className = 'segment-card' + (seg.segment_index === activeSegmentIndex ? ' active-chunk' : '');
            card.dataset.chunk = seg.segment_index;

            const duration = seg.actual_duration_seconds
                ? `${seg.actual_duration_seconds.toFixed(0)}s`
                : seg.estimated_duration_seconds
                    ? `~${seg.estimated_duration_seconds.toFixed(0)}s`
                    : '';

            let hintsHtml = '';
            if (seg.animation_hints && seg.animation_hints.length > 0) {
                hintsHtml = '<div class="segment-hints">';
                for (const hint of seg.animation_hints) {
                    hintsHtml += `<span class="hint-badge" data-type="${this.escHtml(hint.type)}">${this.escHtml(hint.type)}: ${this.escHtml(hint.description)}</span>`;
                }
                hintsHtml += '</div>';
            }

            card.innerHTML = `
                <div class="segment-header">
                    <span class="segment-title">${this.escHtml(seg.section_title)}</span>
                    <span class="segment-duration">${duration}</span>
                </div>
                <div class="segment-narration">${this.escHtml(seg.narration_text)}</div>
                ${hintsHtml}
            `;
            div.appendChild(card);
        }
    },

    renderPipelineStages(currentStage, status) {
        const container = this.$('pipeline-stages');
        container.style.display = 'flex';
        const stages = ['loading', 'scripting', 'annotating', 'voiceover', 'animation', 'compositing', 'done'];
        const currentIdx = stages.indexOf(currentStage);

        stages.forEach((stage, i) => {
            const el = container.querySelector(`[data-stage="${stage}"]`);
            if (!el) return;

            el.classList.remove('active', 'completed', 'failed');

            if (status === 'failed' && i === currentIdx) {
                el.classList.add('failed');
            } else if (i < currentIdx) {
                el.classList.add('completed');
            } else if (i === currentIdx) {
                el.classList.add('active');
            }
        });

        // Update connectors
        const connectors = container.querySelectorAll('.stage-connector');
        connectors.forEach((conn, i) => {
            conn.classList.remove('completed');
            if (i < currentIdx) {
                conn.classList.add('completed');
            }
        });
    },

    showPipelineProgress(data) {
        const bar = this.$('pipeline-progress');
        const fill = this.$('pipeline-progress-fill');
        const label = this.$('pipeline-progress-label');
        bar.style.display = 'block';

        const stageProgress = data.stage_progress || 0;
        fill.style.width = `${(stageProgress * 100).toFixed(0)}%`;
        label.textContent = data.message || '';

        if (data.stage) {
            this.renderPipelineStages(data.stage, data.status);
        }

        if (data.status === 'completed' || data.status === 'failed') {
            setTimeout(() => { bar.style.display = 'none'; }, 2000);
        }
    },

    scrollToChunk(index) {
        const el = document.querySelector(`.segment-card[data-chunk="${index}"], .section[data-chunk="${index}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },

    highlightChunk(index) {
        document.querySelectorAll('.active-chunk').forEach(el => el.classList.remove('active-chunk'));
        const el = document.querySelector(`.segment-card[data-chunk="${index}"], .section[data-chunk="${index}"]`);
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

    setPlayerVisible(show) {
        this.$('player').style.display = show ? 'block' : 'none';
        this.$('export-section').style.display = show ? 'block' : 'none';
    },

    showVideoPlayer(url) {
        const section = this.$('video-player-section');
        const video = this.$('video-player');
        section.style.display = 'block';
        video.src = url;
        this.$('btn-export-video').style.display = 'block';
    },

    hideVideoPlayer() {
        const section = this.$('video-player-section');
        section.style.display = 'none';
        this.$('video-player').src = '';
        this.$('btn-export-video').style.display = 'none';
    },

    updatePlayButton(playing) {
        this.$('btn-play').innerHTML = playing ? '&#9646;&#9646;' : '&#9654;';
    },

    updateChunkIndicator(current, total) {
        this.$('chunk-current').textContent = current;
        this.$('chunk-total').textContent = total;
    },

    escHtml(s) {
        if (!s) return '';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    },
};

class AudioMixer {
    constructor() {
        this.ctx = null;
        this.speechEl = document.getElementById('audio-speech');
        this.musicEl = document.getElementById('audio-music');
        this.speechGain = null;
        this.musicGain = null;
        this._initialized = false;
    }

    init() {
        if (this._initialized) return;
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();

        const speechSource = this.ctx.createMediaElementSource(this.speechEl);
        this.speechGain = this.ctx.createGain();
        speechSource.connect(this.speechGain).connect(this.ctx.destination);

        const musicSource = this.ctx.createMediaElementSource(this.musicEl);
        this.musicGain = this.ctx.createGain();
        musicSource.connect(this.musicGain).connect(this.ctx.destination);

        this._initialized = true;
    }

    setSpeechVolume(v) {
        if (this.speechGain) this.speechGain.gain.value = v;
    }

    setMusicVolume(v) {
        if (this.musicGain) this.musicGain.gain.value = v;
    }

    async playSpeech(url) {
        this.init();
        if (this.ctx.state === 'suspended') await this.ctx.resume();
        this.speechEl.src = url;
        return this.speechEl.play();
    }

    pauseSpeech() {
        this.speechEl.pause();
    }

    resumeSpeech() {
        this.init();
        if (this.ctx.state === 'suspended') this.ctx.resume();
        return this.speechEl.play();
    }

    setMusic(url) {
        this.init();
        if (this.musicEl.src !== url) {
            this.musicEl.src = url;
        }
    }

    playMusic() {
        this.init();
        if (this.ctx.state === 'suspended') this.ctx.resume();
        if (this.musicEl.src) this.musicEl.play();
    }

    pauseMusic() {
        this.musicEl.pause();
    }

    get isPlaying() {
        return !this.speechEl.paused;
    }

    onSpeechEnded(cb) {
        this.speechEl.onended = cb;
    }
}

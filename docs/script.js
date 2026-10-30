const examples = [
  { file: 'stage-replacement', poster: 'stage-replacement', category: '01 / Environment', title: 'From green screen to main stage', description: 'A green-screen performance becomes a concert-stage scene.', label: 'Original and Ripple comparison: green-screen performer on a concert stage' },
  { file: 'savanna-rhino', poster: 'savanna-rhino-reveal', category: '02 / Scene + subject', title: 'A different world, in the same shot', description: 'A green pasture and cow become a dry savanna and rhinoceros.', label: 'Original and Ripple comparison: pasture and cow changed to savanna and rhinoceros' },
  { file: 'bottle-replacement', poster: 'bottle-replacement', category: '03 / Product', title: 'Change the product in the frame', description: 'A wine bottle becomes a green gin bottle.', label: 'Original and Ripple comparison: wine bottle changed to a green gin bottle' },
  { file: 'character-swap', poster: 'character-swap', category: '04 / Character', title: 'A new character takes the lead', description: 'A torch-bearing adventurer becomes a bee-costumed character.', label: 'Original and Ripple comparison: adventurer changed to a bee-costumed character' },
];

const player = document.querySelector('#example-video');
const choices = document.querySelectorAll('.example-choice');
choices.forEach((button) => {
  button.addEventListener('click', () => {
    if (button.getAttribute('aria-pressed') === 'true') return;
    const example = examples[Number(button.dataset.example)];
    const wasPlaying = !player.paused;
    player.pause();
    player.poster = `assets/${example.poster}.webp`;
    player.querySelector('source').src = `assets/${example.file}.mp4`;
    player.querySelector('a').href = `assets/${example.file}.mp4`;
    player.setAttribute('aria-label', example.label);
    document.querySelector('#example-category').textContent = example.category;
    document.querySelector('#example-title').textContent = example.title;
    document.querySelector('#example-description').textContent = example.description;
    document.querySelector('#example-link').href = `assets/${example.file}.mp4`;
    choices.forEach((choice) => {
      const selected = choice === button;
      choice.classList.toggle('active', selected);
      choice.setAttribute('aria-pressed', String(selected));
    });
    player.load();
    if (wasPlaying) player.play().catch(() => {});
  });
});

if (window.RIPPLE_DEMO_URL) {
  const url = new URL(window.RIPPLE_DEMO_URL);
  let videoId;
  if (url.hostname === 'youtu.be') videoId = url.pathname.slice(1);
  if (['youtube.com', 'www.youtube.com'].includes(url.hostname)) {
    videoId = url.searchParams.get('v') || url.pathname.match(/^\/(?:embed|shorts)\/([^/]+)$/)?.[1];
  }
  if (videoId && /^[\w-]{11}$/.test(videoId)) {
    const container = document.querySelector('#demo-embed');
    const title = document.createElement('h2');
    title.textContent = 'The full walkthrough';
    title.id = 'demo-title';
    const frame = document.createElement('iframe');
    frame.src = `https://www.youtube-nocookie.com/embed/${videoId}`;
    frame.title = 'H3 Ripple — full demo and walkthrough';
    frame.loading = 'lazy';
    frame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
    frame.allowFullscreen = true;
    document.querySelector('#demo-placeholder').remove();
    container.append(title, frame);
    container.hidden = false;
  }
}

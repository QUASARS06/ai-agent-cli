let overlay = null;

document.addEventListener('mouseup', handleSelection);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') removeOverlay();
});

async function handleSelection(e) {
  // Ignore clicks inside the overlay itself
  if (overlay && overlay.contains(e.target)) return;

  const selection = window.getSelection().toString().trim();
  if (!selection || selection.includes(' ') || selection.length > 40) return;

  const word = selection.toLowerCase().replace(/^[.,!?;:"'()\[\]{}]+|[.,!?;:"'()\[\]{}]+$/g, '');
  if (!word) return;

  showOverlay(word);
  
  try {
    const response = await fetch(`https://api.dictionaryapi.dev/api/v2/entries/en/${word}`);
    if (!response.ok) throw new Error();
    const data = await response.json();
    const definition = data[0].meanings[0].definitions[0].definition;
    updateOverlay(word, definition);
  } catch (err) {
    updateOverlay(word, 'No definition found.');
  }
}

function showOverlay(word) {
  removeOverlay();
  overlay = document.createElement('div');
  Object.assign(overlay.style, {
    position: 'fixed',
    top: '20px',
    right: '20px',
    width: '300px',
    padding: '15px',
    backgroundColor: 'white',
    color: 'black',
    border: '1px solid #ccc',
    borderRadius: '8px',
    boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
    zIndex: '2147483647',
    fontFamily: 'sans-serif',
    fontSize: '14px',
    lineHeight: '1.4'
  });

  overlay.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
      <strong style="font-size:16px;color:black;">${word}</strong>
      <button id="qd-close" style="border:none;background:none;cursor:pointer;font-size:20px;padding:0;line-height:1;color:black;">&times;</button>
    </div>
    <div id="qd-content" style="color:black;">Loading...</div>
  `;

  document.body.appendChild(overlay);
  overlay.querySelector('#qd-close').onclick = (e) => {
    e.stopPropagation();
    removeOverlay();
  };
}

function updateOverlay(word, text) {
  const content = document.getElementById('qd-content');
  if (content) {
    content.textContent = text;
  }
}

function removeOverlay() {
  if (overlay) {
    overlay.remove();
    overlay = null;
  }
}

const form = document.querySelector('#form');
const result = document.querySelector('#result');
const submit = document.querySelector('#submit');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
form.addEventListener('submit', async event => {
  event.preventDefault();
  submit.disabled = true;
  result.textContent = 'Préparation de la conversion…';
  try {
    const response = await fetch('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: document.querySelector('#url').value, format: new FormData(form).get('format')})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    while (true) {
      await pause(2000);
      const poll = await fetch(`/api/jobs/${data.id}`);
      const job = await poll.json();
      if (!poll.ok) throw new Error(job.error);
      if (job.status === 'error') throw new Error('Conversion impossible. Vérifie que la vidéo est accessible et réessaie.');
      if (job.status === 'ready') {
        result.replaceChildren();
        const link = document.createElement('a');
        link.className = 'download';
        link.href = `/api/jobs/${data.id}/download`;
        link.textContent = '↓ Télécharger le fichier';
        const note = document.createElement('p');
        note.textContent = 'Prêt ! Ce lien expire dans une heure.';
        result.append(link, note);
        break;
      }
      result.textContent = job.status === 'queued' ? 'En attente de conversion…' : 'Conversion en cours… Cela peut prendre quelques minutes.';
    }
  } catch (error) { result.textContent = error.message || 'Une erreur est survenue.'; }
  finally { submit.disabled = false; }
});

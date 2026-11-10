'use strict';
const $ = selector => document.querySelector(selector);
let me = null;
const mode = location.pathname;
const token = location.hash.slice(1);
if (token) history.replaceState(null, '', location.pathname);
function message(text, error = false) { $('#message').textContent = text; $('#message').classList.toggle('error', error); }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache: 'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':me?.csrf || ''},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) { if (response.status === 401 && mode === '/admin') location.replace('/login'); throw new Error(data.error || 'Erreur du serveur.'); }
  return data;
}
function button(text, action) { const node=document.createElement('button'); node.type='button'; node.textContent=text; node.className='danger'; node.onclick=async()=>{node.disabled=true;try{await action();await loadAdmin();message('Accès révoqué.');}catch(e){message(e.message,true);}finally{node.disabled=false;}};return node; }
function cell(row, text) { const node=document.createElement('td'); node.textContent=text; row.append(node); return node; }
async function loadAdmin() {
  const [users,invites]=await Promise.all([api('/auth/admin/users'),api('/auth/admin/invites')]);
  $('#users').replaceChildren();
  for(const user of users){const row=document.createElement('tr');cell(row,user.username);cell(row,user.admin?'Administrateur':user.active?'Actif':'Révoqué');const action=cell(row,'');if(user.active&&!user.admin)action.append(button('Révoquer',()=>api('/auth/admin/revoke-user',{id:user.id})));$('#users').append(row);}
  $('#invites').replaceChildren();
  for(const invite of invites){const row=document.createElement('tr');cell(row,invite.label);cell(row,invite.used_by?'Utilisée par '+invite.used_by:invite.revoked?'Révoquée':invite.expires*1000<=Date.now()?'Expirée':'Disponible');cell(row,new Date(invite.expires*1000).toLocaleString());const action=cell(row,'');if(!invite.revoked&&!invite.used_by&&invite.expires*1000>Date.now())action.append(button('Révoquer',()=>api('/auth/admin/revoke-invite',{id:invite.id})));$('#invites').append(row);}
}
async function start() {
  if(mode==='/admin'){
    me=await api('/auth/me');$('#administration').hidden=false;
    const home=document.createElement('a');home.href='/';home.textContent='Convertisseur';$('#account').append(home);
    const logout=document.createElement('button');logout.textContent='Déconnexion';logout.className='secondary';logout.onclick=async()=>{try{await api('/auth/logout',{});location.replace('/login');}catch(e){message(e.message,true);}};$('#account').append(logout);
    await loadAdmin();return;
  }
  $('#credentials').hidden=false;
  if(mode==='/setup'){
    $('#title').textContent='Bienvenue, tlacassi';$('#description').textContent='Choisis ton mot de passe administrateur : au moins 8 caractères.';$('#username').value='tlacassi';$('#username').readOnly=true;$('#submit').textContent='Activer mon compte admin';
  }else if(mode==='/invite'){$('#title').textContent='Créer mon compte';$('#description').textContent='Choisis ton identifiant et un mot de passe d’au moins 8 caractères.';$('#submit').textContent='Créer mon compte';}
  if(mode!=='/login'){$('#password').autocomplete='new-password';$('#confirm-label').hidden=false;$('#confirm').required=true;if(!token){$('#submit').disabled=true;message('Le lien est incomplet. Rouvre le lien reçu avec son code secret.',true);}}
}
$('#credentials-form').onsubmit=async event=>{event.preventDefault();$('#submit').disabled=true;message('');try{if(mode!=='/login'&&$('#password').value!==$('#confirm').value)throw new Error('Les mots de passe ne correspondent pas.');await api(mode==='/setup'?'/auth/setup':mode==='/invite'?'/auth/register':'/auth/login',{username:$('#username').value,password:$('#password').value,token});location.replace(mode==='/setup'?'/admin':'/');}catch(e){message(e.message,true);$('#submit').disabled=false;}};
$('#invite-form').onsubmit=async event=>{event.preventDefault();const submit=event.target.querySelector('button');submit.disabled=true;try{const invite=await api('/auth/admin/invites',{label:$('#label').value});$('#invite-link').value=location.origin+'/invite#'+invite.token;$('#new-link').hidden=false;$('#label').value='';await loadAdmin();message('Invitation créée.');}catch(e){message(e.message,true);}finally{submit.disabled=false;}};
$('#copy').onclick=async()=>{try{await navigator.clipboard.writeText($('#invite-link').value);message('Lien copié.');}catch{message('Sélectionne le lien pour le copier manuellement.');}};
start().catch(e=>message(e.message,true));

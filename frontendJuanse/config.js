// Where the Flask backend lives. On your own computer the pages talk to the local backend;
// everywhere else (the live site) they talk to the hosted one.
const IS_LOCAL = ['localhost', '127.0.0.1', ''].includes(window.location.hostname);
const API_BASE_URL = IS_LOCAL ? 'http://127.0.0.1:5000' : 'https://api.dalbagnihealth.com';

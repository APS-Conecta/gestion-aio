const channel = new BroadcastChannel('tab')

channel.postMessage('second-tab')
// note that listener is added after posting the message

channel.addEventListener('message', (msg) => {
    if (msg.data === 'second-tab') {
        // message received from 2nd tab
        document.getElementById('overlay').classList.add('loading')
        alert('No se puede abrir AIO en varias pestañas a la vez. Puede usarlo aquí recargando la página.')
    }
});
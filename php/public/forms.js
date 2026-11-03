"use strict";

(function (){
  let lastError;

  function showError(message) {
    const body = document.getElementsByTagName('body')[0]
    const toast = document.createElement("div")
    toast.className = "toast error"
    toast.prepend(message)
    if (lastError) {
      lastError.remove()
    }
    lastError = toast
    body.prepend(toast)
    setTimeout(toast.remove.bind(toast), 10000)
  }

  function handleEvent(e) {
    const xhr = e.target;
    if (xhr.status === 201) {
      window.location.replace(xhr.getResponseHeader('Location'));
    } else if (xhr.status === 422) {
      disableSpinner()
      showError(xhr.response);
    } else if (xhr.status === 500) {
      showError("Error del servidor. Revise los registros del mastercontainer para ver los detalles. Esta página se recargará automáticamente en 10 s; luego podrá revisar esos registros.");
      // Reload after 10s since it is expected that the updated view is shown (e.g. after starting containers)
      setTimeout(function(){
        window.location.reload(true);
      }, 10000);
    } else {
      // If the responose is not one of the above, we should reload to show the latest content
      window.location.reload(true);
    }
  }

  function enableSpinner() {
    document.getElementById('overlay').classList.add('loading');
  }

  function disableSpinner() {
    document.getElementById('overlay').classList.remove('loading');
  }

  function initForm(form) {
    function submit(event)
    {
      if (lastError) {
        lastError.remove()
      }
      let xhr = new XMLHttpRequest();
      xhr.addEventListener('load', handleEvent);
      xhr.addEventListener('error', () => showError("No se pudo comunicar con el servidor."));
      xhr.addEventListener('error', () => disableSpinner());
      xhr.open(form.method, form.getAttribute("action"));
      xhr.setRequestHeader('Content-type', 'application/x-www-form-urlencoded');
      enableSpinner();
      xhr.send(new URLSearchParams(new FormData(form)));
      event.preventDefault();
    }

    form.onsubmit = submit;
  }

  function initForms() {
    const forms = document.querySelectorAll('form.xhr')
    for (const form of forms) {
      initForm(form);
    }
    const overlayLogForms = document.querySelectorAll('form[target="overlay-log"]')
    for (const form of overlayLogForms) {
        form.onsubmit = function() {
            enableSpinner();
            document.getElementById('overlay-log')?.classList.add('visible');
            // Reload the page after the response was fully loaded into the iframe.
            document.querySelector('iframe[name="overlay-log"]').addEventListener('load', () => {
                location.reload(true);
            });
        };
    }
  }

  if (document.readyState === 'loading') {
    // Loading hasn't finished yet
    document.addEventListener('DOMContentLoaded', initForms);
  } else {  // `DOMContentLoaded` has already fired
    initForms();
  }
})()

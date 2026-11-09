/* -*- Mode: JS; tab-width: 2; indent-tabs-mode: nil; js-indent-level: 2; fill-column: 100 -*- */
// SPDX-License-Identifier: MIT
//
// Runs inside the ZetaOffice web worker, where `zetajs` drives LibreOffice over UNO.
// Loaded from index.html via Module.uno_scripts, so `Module.zetajs` is already pending here.
//
// This is a read-only viewer: the bytes came from the workspace and any edit made here would
// die with the frame, so editing is locked instead of pretending to save.

'use strict';

let zetajs, css;
let context, desktop, xModel, ctrl;

function post(cmd, payload) {
  zetajs.mainPort.postMessage(Object.assign({ cmd }, payload));
}

function boot() {
  context = zetajs.getUnoComponentContext();
  desktop = css.frame.Desktop.create(context);

  zetajs.mainPort.onmessage = function (event) {
    switch (event.data.cmd) {
    case 'open':
      openDocument(event.data.filename);
      break;
    default:
      break;
    }
  };

  post('thr_running');
}

function openDocument(filename) {
  let url = 'file:///tmp/office/' + filename;
  try {
    xModel = desktop.loadComponentFromURL(url, '_default', 0, []);
  } catch (error) {
    post('fatal', { message: 'LibreOffice 无法打开该文件: ' + (error && error.message || error) });
    return;
  }
  if (!xModel) {
    post('fatal', { message: 'LibreOffice 返回了空文档: ' + filename });
    return;
  }

  try {
    ctrl = xModel.getCurrentController();
    ctrl.getFrame().getContainerWindow().FullScreen = true;
    // Chrome that only makes sense for editing is hidden; the grid and its headers stay.
    ctrl.getFrame().LayoutManager.hideElement('private:resource/menubar/menubar');
    ctrl.getFrame().LayoutManager.hideElement('private:resource/statusbar/statusbar');
  } catch (error) {
    /* A missing element is not fatal — the document still displays. */
  }

  try {
    // No save target exists inside the WASM sandbox, so refuse edits outright.
    xModel.lockControllers();
  } catch (error) {
    /* Older cores lack lockControllers; the hidden chrome still keeps the viewer calm. */
  }

  post('ui_ready', {});
}

Module.zetajs.then(function (pZetajs) {
  zetajs = pZetajs;
  css = zetajs.uno.com.sun.star;
  boot();
});

/* vim:set shiftwidth=2 softtabstop=2 expandtab cinoptions=b1,g0,N-s cinkeys+=0=break: */

import Jupyter from 'base/js/namespace';
import dialog from 'base/js/dialog';
import React from 'react';
import ReactDOM from 'react-dom';

import {
  Inliner,
  notebook_context
} from './main';
import {
  set_env,
  Env
} from './env';
import {
  NotebookState
} from './state';

const ALWAYS_OPEN = true;
//mobx.observable(localStorage.getItem('INLINER_DEV_MODE'));

class NotebookEnv extends Env {
  constructor(state) {
    super();

    this._state = state;

    let update_current_cell = () => {
      let cell = Jupyter.notebook.get_selected_cell();
      this._state.current_cell = cell.cell_id;
    };

    // https://github.com/jupyter/notebook/blob/76a323e677b7080a1e9a88437d6b5cea6cc0403b/notebook/static/notebook/js/notebook.js#L332
    ['select.Cell', 'set_dirty.Notebook'].forEach((event) => {
      Jupyter.notebook.events.on(event, () => update_current_cell());
    });
  }

  show_error(error) {
    dialog.modal({
      title: 'Inliner error',
      body: $(error),
      buttons: {
        'Dismiss': {}
      }
    });
  }

  get_and_insert() {
    let cell = Jupyter.notebook.get_selected_cell();
    let new_cell = Jupyter.notebook.insert_cell_below();
    Jupyter.notebook.select_next();

    new_cell.element.css('background', 'rgb(242, 251, 252)');

    this._state.current_cell = new_cell.cell_id;

    return {
      text: cell.get_text(),
      cell_id: new_cell.cell_id,
      set_cell_text: (text) => new_cell.set_text(text)
    };
  }

  get_selected_text() {
    const cell = Jupyter.notebook.get_selected_cell();
    return cell.code_mirror.getSelection().toString();
  }

  create_new_cell(text) {
    let new_cell = Jupyter.notebook.insert_cell_below();
    Jupyter.notebook.select_next();
    new_cell.set_text(text);
  }

  execute(pysrc, wait_output) {
    pysrc = pysrc.trim();
    return new Promise((resolve, reject) => {
      Jupyter.notebook.kernel.execute(pysrc, {
        shell: {
          reply: (reply) => {
            if (!wait_output) {
              if (reply.content.status == 'error') {
                console.error(reply);
                reject(this.format_trace(reply.content.traceback));
              } else {
                resolve(reply);
              }
            }
          },
        },
        iopub: {
          output: (reply) => {
            if (wait_output) {
              if (reply.msg_type == 'error') {
                console.error(reply);
                reject(this.format_trace(reply.content.traceback));
              } else {
                // HACK: if a call both prints to stdout and raises
                // an exception, then the iopub.output callback receives
                // two messages, and the stdout comes before the error.
                // We can wait a little bit to see if we get the error first
                // before resolving.
                setTimeout(() => {
                  resolve(reply.content.text)
                }, 100);
              }
            }
          }
        }
      });
    });
  }
}

class NotebookInliner extends React.Component {
  state = {
    show: ALWAYS_OPEN,
  }

  margin = 20

  constructor(props) {
    super(props);
    this._state = new NotebookState();
    set_env(new NotebookEnv(this._state));
  }

  componentDidMount() {
    let toggle_show = () => {
      this.setState({
        show: !this.state.show
      })
    };

    Jupyter.toolbar.add_buttons_group([
      Jupyter.keyboard_manager.actions.register({
        'help': 'Inliner',
        'icon': 'fa-bolt',
        'handler': toggle_show,
      }, 'toggle-inliner', 'inliner')
    ]);
  }

  _compute_dimensions() {
    const notebook = document.querySelector('#notebook-container');
    const notebook_rect = notebook.getBoundingClientRect();
    return {
      width: (window.innerWidth - notebook_rect.width) / 2 - this.margin * 2,
      top: notebook_rect.top,
      right: this.margin
    }
  }

  render() {
    let style = {
      display: this.state.show ? 'block' : 'none',
      ...this._compute_dimensions()
    };

    return <div className='inliner-notebook' style={style}>
      <notebook_context.Provider value={this._state}>
        <Inliner />
      </notebook_context.Provider>
    </div>;
  }
}

export async function load_ipython_extension() {
  await Jupyter.notebook.config.loaded;

  let container = document.createElement('div');
  document.body.appendChild(container);

  ReactDOM.render(<NotebookInliner />, container);

  console.log('[Inliner] Extension initialized');
};

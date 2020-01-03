export class Env {
  format_trace(traceback) {
    var trace = traceback.join('\n');
    // https://stackoverflow.com/questions/25245716/remove-all-ansi-colors-styles-from-strings/29497680
    var trace_no_color = trace.replace(
      /[\u001b\u009b][[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]/g, '');
    return trace_no_color;
  };
}

let ENV;
export let get_env = () => ENV;
export let set_env = (env) => { ENV = env; }

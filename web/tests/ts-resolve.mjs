// Lets Node's built-in TypeScript support load the app's extensionless imports ("./util" -> "./util.ts"), which the
// bundler resolves in the real build. Used only by `npm test`.
import { registerHooks } from "node:module";

registerHooks({
  resolve(spec, ctx, next) {
    try {
      return next(spec, ctx);
    } catch (e) {
      if (/^\.\.?\//.test(spec) && !/\.[cm]?[jt]sx?$/.test(spec)) return next(`${spec}.ts`, ctx);
      throw e;
    }
  },
});

import {bootWorkspace} from "./workspaceBoot.js";

/**
 * Boots the workspace, then puts back the `OperatorSession` a test put on its fake window: the boot module sets the real session controls, and these tests want
 * their own stand-in (the tools read it when they use it). A window with no `OperatorSession` gets the real controls.
 */
export function bootKeepingSession(win) {
  const stand_in = win.OperatorSession;
  const workspace = bootWorkspace(win);
  if (stand_in !== undefined) win.OperatorSession = stand_in;
  return workspace;
}

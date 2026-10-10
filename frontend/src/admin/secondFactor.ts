import { createContext, useContext } from "react";

/**
 * True on an installation whose server runs with the administrators' second factor switched off
 * (`QD_ADMIN_SECOND_FACTOR=off`). The server says so in the door's status; nothing here guesses it.
 * The screens that would ask for an authenticator code read it and ask for none.
 */
export const SecondFactorOff = createContext(false);

export function useSecondFactorOff(): boolean {
  return useContext(SecondFactorOff);
}

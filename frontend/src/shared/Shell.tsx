import type { ReactNode } from "react";

type ShellProps = {
  title: string;
  children?: ReactNode;
};

/** Placeholder frame shared by the three entry points until the real screens arrive (story S2.2). */
export function Shell({ title, children }: ShellProps) {
  return (
    <main>
      <h1>{title}</h1>
      {children}
    </main>
  );
}

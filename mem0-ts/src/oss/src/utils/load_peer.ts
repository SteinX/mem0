function missingPeerError(
  pkg: string,
  label: string,
  error: unknown,
): Error | undefined {
  const code = (error as { code?: string } | undefined)?.code;
  const message = error instanceof Error ? error.message : "";
  const missing = message.match(
    /Cannot find (?:module|package) ['"]([^'"]+)['"]/,
  )?.[1];
  if (
    (code === "ERR_MODULE_NOT_FOUND" || code === "MODULE_NOT_FOUND") &&
    (missing === pkg || missing?.startsWith(`${pkg}/`))
  ) {
    return Object.assign(
      new Error(
        `The '${pkg}' package is required to use the ${label}. Install it with: npm install ${pkg}`,
      ),
      { cause: error },
    );
  }
}

export async function loadPeer(
  pkg: string,
  label: string,
  load: () => Promise<any>,
): Promise<any> {
  try {
    return await load();
  } catch (error) {
    throw missingPeerError(pkg, label, error) ?? error;
  }
}

export function loadPeerSync<T>(pkg: string, label: string, load: () => T): T {
  try {
    return load();
  } catch (error) {
    throw missingPeerError(pkg, label, error) ?? error;
  }
}

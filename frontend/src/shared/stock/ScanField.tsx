import { type FormEvent, useEffect, useRef, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { useLatest } from "../hooks";
import { SearchIcon } from "../icons";
import { FieldError } from "../workspace/parts";
import {
  type BarcodeProblem,
  type CameraSupport,
  cameraSupport,
  readBarcode,
  ScanBuffer,
  type ScanHost,
  searchIntent,
  stopStream,
} from "./barcode";
import "./messages";
import "./stock.css";

const PROBLEM_TEXT: Readonly<Record<BarcodeProblem, MessageKey>> = {
  empty: "stock.scan.problem.empty",
  too_long: "stock.scan.problem.long",
  check_digit: "stock.scan.problem.check",
  characters: "stock.scan.problem.characters",
};

/** Why the camera cannot read a label here, in words; manual entry and a scanner always remain. */
const UNAVAILABLE_TEXT = {
  detector: "stock.scan.camera.none",
  camera: "stock.scan.camera.noCamera",
  formats: "stock.scan.camera.noFormats",
} as const satisfies Record<string, MessageKey>;

const SCAN_EVERY_MS = 250;

type Stream = { getTracks: () => { stop: () => void }[] };
type Media = { getUserMedia: (constraints: unknown) => Promise<Stream> };

/**
 * The camera, open until it reads a label or is closed. The picture stays in the page: frames go to the
 * browser's own detector and nowhere else. The stream is stopped when the panel closes for any reason,
 * so the camera never stays on behind a screen that no longer shows it.
 */
function CameraScanner({
  support,
  media,
  onCode,
  onClose,
}: {
  support: Extract<CameraSupport, { ok: true }>;
  media: Media;
  onCode: (code: string) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const video = useRef<HTMLVideoElement | null>(null);
  const [failure, setFailure] = useState<"denied" | "failed" | null>(null);
  const found = useLatest(onCode);
  // The detector and the camera API as they were given when the panel opened.
  const given = useLatest({ support, media });

  useEffect(() => {
    let stream: Stream | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    const detector = given.current.support.create();

    const look = () => {
      const element = video.current;
      if (closed || element === null) {
        return;
      }
      detector.detect(element).then(
        (codes) => {
          const code = codes.find((candidate) => candidate.rawValue !== "")?.rawValue;
          if (closed) {
            return;
          }
          if (code !== undefined) {
            found.current(code);
          } else {
            timer = setTimeout(look, SCAN_EVERY_MS);
          }
        },
        // A frame that is not ready yet is refused by the detector; the next one is tried.
        () => {
          if (!closed) {
            timer = setTimeout(look, SCAN_EVERY_MS);
          }
        },
      );
    };

    given.current.media.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false }).then(
      (opened) => {
        if (closed) {
          stopStream(opened);
          return;
        }
        stream = opened;
        const element = video.current;
        if (element !== null) {
          element.srcObject = opened as unknown as MediaStream;
          // Some browsers refuse to start a video by itself; the attributes below ask for it as well.
          Promise.resolve()
            .then(() => element.play())
            .catch(() => undefined);
        }
        timer = setTimeout(look, SCAN_EVERY_MS);
      },
      (error: unknown) => {
        if (!closed) {
          const name = error instanceof Error ? error.name : "";
          setFailure(name === "NotAllowedError" || name === "SecurityError" ? "denied" : "failed");
        }
      },
    );

    return () => {
      closed = true;
      if (timer !== null) {
        clearTimeout(timer);
      }
      stopStream(stream);
    };
  }, [found, given]);

  return (
    <div className="scan" role="group" aria-label={t("stock.scan.camera.title")}>
      {failure === null ? (
        <>
          <video ref={video} className="scan__video" muted playsInline autoPlay aria-label={t("stock.scan.camera.title")} />
          <p className="hint">{t("stock.scan.camera.hint")}</p>
        </>
      ) : (
        <p className="notice notice--error" role="alert">
          {t(failure === "denied" ? "stock.scan.camera.denied" : "stock.scan.camera.failed")}
        </p>
      )}
      <p className="actions">
        <button type="button" className="button" onClick={onClose}>
          {t("stock.scan.camera.close")}
        </button>
      </p>
    </div>
  );
}

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/**
 * Scan or type: the one field a barcode enters through, in a search and in the lines of a document.
 *
 *  - A scanner that acts as a keyboard types the code fast and ends with Enter: that is a code,
 *    whatever it looks like, in this field or, when no field has the focus, anywhere on the page.
 *  - Typed text followed by Enter is a code when it is a retail code with a right check digit, and
 *    otherwise words to search by (`onWords`); without `onWords` the field takes codes only.
 *  - The camera is offered only where the browser can read labels; where it cannot, a line says so.
 *
 * A code that cannot be one (a wrong check digit, a letter outside ASCII) is refused here with the
 * reason, and nothing is looked up.
 */
export function ScanField({
  id,
  label,
  disabled = false,
  onCode,
  onWords,
  host,
  now = () => performance.now(),
}: {
  id: string;
  label: string;
  disabled?: boolean;
  onCode: (code: string) => void;
  onWords?: ((words: string) => void) | undefined;
  /** The browser's own objects; tests pass theirs. */
  host?: ScanHost | undefined;
  now?: () => number;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<BarcodeProblem | null>(null);
  const [support, setSupport] = useState<CameraSupport | null>(null);
  const [cameraOpen, setCameraOpen] = useState(false);
  const inField = useRef(new ScanBuffer());
  const scanned = useRef<string | null>(null);
  const latest = useLatest({ onCode, disabled, host, now });

  useEffect(() => {
    let wanted = true;
    cameraSupport(latest.current.host).then((found) => {
      if (wanted) {
        setSupport(found);
      }
    });
    return () => {
      wanted = false;
    };
    // The browser does not change while the page is open: asked once.
  }, [latest]);

  const take = (raw: string) => {
    const read = readBarcode(raw);
    if (!read.ok) {
      setProblem(read.problem);
      return;
    }
    setProblem(null);
    setText("");
    latest.current.onCode(read.code);
  };
  const taking = useLatest(take);

  // A scan while nothing has the focus: the scanner types into the page, and the code is taken here.
  useEffect(() => {
    const outside = new ScanBuffer();
    const onKey = (event: KeyboardEvent) => {
      if (latest.current.disabled || isEditable(event.target) || event.ctrlKey || event.altKey || event.metaKey) {
        outside.reset();
        return;
      }
      const code = outside.feed(event.key, latest.current.now());
      if (code !== null) {
        event.preventDefault();
        taking.current(code);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [latest, taking]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const code = scanned.current;
    scanned.current = null;
    const typed = text.trim();
    if (code === null && typed === "") {
      if (onWords) {
        onWords("");
      } else {
        setProblem("empty");
      }
      return;
    }
    if (code !== null) {
      take(code);
    } else if (onWords && searchIntent(typed, false) === "words") {
      setProblem(null);
      onWords(typed);
    } else {
      take(typed);
    }
  };

  const media = (host ?? (typeof window === "undefined" ? undefined : (window as unknown as ScanHost)))?.navigator
    ?.mediaDevices as Media | undefined;

  return (
    <div className="scanfield">
      <form className="search search--icon" role="search" onSubmit={onSubmit} noValidate>
        <SearchIcon />
        <input
          id={id}
          type="search"
          className="input"
          value={text}
          maxLength={100}
          autoComplete="off"
          disabled={disabled}
          aria-label={label}
          placeholder={label}
          aria-invalid={problem !== null}
          aria-describedby={`${id}-error ${id}-camera`}
          onKeyDown={(event) => {
            const code = inField.current.feed(event.key, now());
            if (event.key === "Enter") {
              scanned.current = code;
            }
          }}
          onChange={(event) => {
            setText(event.target.value);
            setProblem(null);
          }}
        />
        <button type="submit" className="button" disabled={disabled}>
          {t(onWords ? "action.search" : "stock.scan.find")}
        </button>
        {support?.ok && !cameraOpen ? (
          <button type="button" className="button" onClick={() => setCameraOpen(true)} disabled={disabled}>
            {t("stock.scan.camera.open")}
          </button>
        ) : null}
      </form>
      <FieldError id={`${id}-error`} message={problem === null ? null : t(PROBLEM_TEXT[problem])} />
      {support !== null && !support.ok ? (
        <p className="field__hint" id={`${id}-camera`}>
          {t(UNAVAILABLE_TEXT[support.why])}
        </p>
      ) : null}
      {support?.ok && cameraOpen && media ? (
        <CameraScanner
          support={support}
          media={media}
          onCode={(code) => {
            setCameraOpen(false);
            take(code);
          }}
          onClose={() => setCameraOpen(false)}
        />
      ) : null}
    </div>
  );
}

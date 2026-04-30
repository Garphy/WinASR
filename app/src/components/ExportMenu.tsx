import { useState, useRef, useEffect, useCallback } from 'react';
import type { TranscriptionResult } from '../types';
import { exportAsText, exportAsMarkdown, downloadFile } from '../utils/export';

interface ExportMenuProps {
  result: TranscriptionResult;
  taskId: string;
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    position: 'relative',
    display: 'inline-block',
  },
  button: {
    padding: '6px 16px',
    fontSize: 13,
    fontWeight: 600,
    color: '#555',
    background: '#f0f0f0',
    border: '1px solid #d0d0d0',
    borderRadius: 6,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  dropdown: {
    position: 'absolute',
    top: '100%',
    right: 0,
    marginTop: 4,
    background: '#fff',
    border: '1px solid #e0e0e0',
    borderRadius: 8,
    boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
    zIndex: 100,
    minWidth: 180,
    overflow: 'hidden',
  },
  item: {
    display: 'block',
    width: '100%',
    padding: '10px 16px',
    fontSize: 14,
    color: '#333',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    textAlign: 'left' as const,
    transition: 'background 0.1s',
  },
};

export default function ExportMenu({ result, taskId }: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const handleExportText = useCallback(() => {
    const content = exportAsText(result);
    downloadFile(content, `transcription-${taskId}.txt`, 'text/plain');
    setOpen(false);
  }, [result, taskId]);

  const handleExportMarkdown = useCallback(() => {
    const content = exportAsMarkdown(result);
    downloadFile(content, `transcription-${taskId}.md`, 'text/markdown');
    setOpen(false);
  }, [result, taskId]);

  return (
    <div ref={wrapperRef} style={styles.wrapper}>
      <button
        style={styles.button}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        📤 导出 ▾
      </button>
      {open && (
        <div style={styles.dropdown} role="menu">
          <button
            style={styles.item}
            role="menuitem"
            onClick={handleExportText}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = '#f5f5f5';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'none';
            }}
          >
            📄 导出为纯文本
          </button>
          <button
            style={styles.item}
            role="menuitem"
            onClick={handleExportMarkdown}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = '#f5f5f5';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'none';
            }}
          >
            📝 导出为 Markdown
          </button>
        </div>
      )}
    </div>
  );
}

import type { ChangeEvent, DragEvent } from "react";
import { BookOpenText, XCircle } from "lucide-react";
import { ACCEPTED_UPLOAD_TYPES, uploadTypeLabel } from "../app/appSupport";
import type { UploadedFileInfo } from "../types";

type UploadAttachmentTrayProps = {
  files: UploadedFileInfo[];
  onRemove: (path: string) => void;
};

type UploadPopoverProps = {
  busy: boolean;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
};

export function UploadAttachmentTray({ files, onRemove }: UploadAttachmentTrayProps) {
  if (files.length === 0) {
    return null;
  }

  return (
    <div className="upload-attachment-tray" aria-label="已上传文件">
      {files.map((file) => (
        <div className="upload-attachment-card" key={file.path}>
          <div className="upload-attachment-icon">
            <BookOpenText size={17} />
          </div>
          <div className="upload-attachment-copy">
            <strong title={file.originalFilename}>{file.originalFilename}</strong>
            <span>{uploadTypeLabel(file)}</span>
          </div>
          <button
            className="upload-attachment-remove"
            onClick={() => onRemove(file.path)}
            type="button"
            aria-label={`移除 ${file.originalFilename}`}
            title="移除"
          >
            <XCircle size={17} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function UploadPopover({ busy, onDrop, onFileChange }: UploadPopoverProps) {
  return (
    <div className="upload-popover glass-panel" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
      <div className="upload-popover-head">
        <strong>上传文件</strong>
        <span>{busy ? "上传中" : "文本 / 代码 / 数据"}</span>
      </div>
      <div className="upload-drop-zone">
        <span>拖拽文件到这里</span>
        <small>或使用下方文件选择</small>
      </div>
      <input
        className="upload-native-input"
        type="file"
        multiple
        accept={ACCEPTED_UPLOAD_TYPES}
        disabled={busy}
        onChange={onFileChange}
      />
    </div>
  );
}

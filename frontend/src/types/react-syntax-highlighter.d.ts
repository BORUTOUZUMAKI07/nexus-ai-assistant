declare module "react-syntax-highlighter" {
  import * as React from "react";

  export interface PrismLightProps {
    language?: string;
    style?: Record<string, unknown>;
    customStyle?: React.CSSProperties;
    showLineNumbers?: boolean;
    codeTagProps?: Record<string, unknown>;
    children: string;
    [key: string]: unknown;
  }

  export const PrismLight: React.FC<PrismLightProps>;
}

declare module "react-syntax-highlighter/dist/esm/styles/prism" {
  export const vscDarkPlus: Record<string, unknown>;
}

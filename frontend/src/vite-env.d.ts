/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_MAP_STYLE_URL_NORMAL?: string;
  readonly VITE_MAP_STYLE_URL_DARK?: string;
  readonly VITE_ARCGIS_API_KEY?: string;
  readonly VITE_MAP_SATELLITE_TILES_URL?: string;
  readonly VITE_MAP_SATELLITE_LABELS_TILES_URL?: string;
  readonly VITE_BAIDU_AK?: string;
  readonly VITE_BAIDU_STYLE_ID?: string;
  readonly VITE_BAIDU_STYLE_ID_NORMAL?: string;
  readonly VITE_BAIDU_STYLE_ID_DARK?: string;
}
interface ImportMeta { readonly env: ImportMetaEnv; }

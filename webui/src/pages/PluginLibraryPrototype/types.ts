export type PluginType = 'workflow' | 'agent' | 'skill' | 'tool' | 'device';

export type CatalogScope =
  | 'workflow'
  | 'agent'
  | 'skill'
  | 'tool'
  | 'device-template'
  | 'device-instance';

export type LocalizedText = { zh: string; en: string };

export type Collection = {
  id: string;
  scope: CatalogScope;
  name: string;
};

export type FacetDefinition = {
  key: string;
  label: LocalizedText;
  options: { value: string; label: LocalizedText }[];
};

export type ScopeConfig = {
  scope: CatalogScope;
  type: PluginType;
  title: LocalizedText;
  description: LocalizedText;
  facets: FacetDefinition[];
};

export type PluginItem = {
  id: string;
  scope: CatalogScope;
  name: string;
  identifier: string;
  description: string;
  tags: string[];
  collectionId: string | null;
  favorite: boolean;
  updatedAt: string;
  attributes: Record<string, string>;
  details: { label: LocalizedText; value: string }[];
};

export type OrganizationState = {
  version: 1;
  groups: Collection[];
  memberships: Record<string, string | null>;
  favorites: string[];
};

export type CatalogQuery = {
  scope: CatalogScope;
  query: string;
  /** all, favorites, ungrouped, or a custom collection ID. */
  collection: string;
  tags: string[];
  facets: Record<string, string[]>;
  sort: 'updated' | 'name';
};

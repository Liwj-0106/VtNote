export interface LibraryFilters {
  query: string;
  source: string;
  status: string;
  collectionId: string;
  unclassified: boolean;
  tagId: string;
  excerptsOnly: boolean;
}

export const emptyLibraryFilters: LibraryFilters = {
  query: "",
  source: "",
  status: "",
  collectionId: "",
  unclassified: false,
  tagId: "",
  excerptsOnly: false,
};

export function hasLibraryFilters(filters: LibraryFilters): boolean {
  return Boolean(
    filters.query.trim() ||
      filters.source ||
      filters.status ||
      filters.collectionId ||
      filters.unclassified ||
      filters.tagId ||
      filters.excerptsOnly,
  );
}

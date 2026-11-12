import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ProjectFolderDialog, { joinFolderPath } from './ProjectFolderDialog';

const { apiGet, apiPost, apiPatch, toastMock } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/api/client', () => ({ default: { get: apiGet, post: apiPost, patch: apiPatch } }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => toastMock }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

interface Listing {
  path: string;
  parent: string | null;
  roots: { name: string; path: string }[];
  entries: { name: string; path: string }[];
  missing?: boolean;
}

const HOME = '/home/u';
const homeListing = (extra: Partial<Listing> = {}): Listing => ({
  path: HOME,
  parent: null,
  roots: [{ name: 'u', path: HOME }],
  entries: [{ name: 'projects', path: `${HOME}/projects` }],
  ...extra,
});

/** What the backend answers, keyed by the `path` it is asked for ('' = no path). */
function serveFolders(byPath: Record<string, Listing | Error>) {
  apiGet.mockImplementation((_url: string, config?: { params?: { path?: string } }) => {
    const answer = byPath[config?.params?.path ?? ''];
    if (!answer) return Promise.reject(new Error(`unexpected path ${config?.params?.path}`));
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve({ data: answer });
  });
}

/** An axios rejection carrying the backend's own error body. */
function httpError(status: number, message: string): Error {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { error: 'HTTPException', message } },
  });
}

const nameInput = () => screen.getByLabelText('history.folderName') as HTMLInputElement;
const directoryInput = () => screen.getByLabelText('history.folderPath') as HTMLInputElement;
const browse = () => fireEvent.click(screen.getByRole('button', { name: 'history.chooseFolder' }));

async function renderDialog() {
  const onSaved = vi.fn();
  render(<ProjectFolderDialog mode="create" onClose={vi.fn()} onSaved={onSaved} />);
  // Let the default-root request and the effect that follows it settle before typing.
  await act(async () => {});
  expect(apiGet).toHaveBeenCalledTimes(1);
  return { onSaved };
}

async function typeName(value: string, expectedDirectory: string) {
  fireEvent.change(nameInput(), { target: { value } });
  await waitFor(() => expect(directoryInput()).toHaveValue(expectedDirectory));
}

describe('ProjectFolderDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('opens the picker above a proposed directory that is not created yet, and keeps that directory', async () => {
    // INC-14B4A77C: Browse on the directory the dialog itself filled in used to fail with a 400.
    serveFolders({
      '': homeListing(),
      [`${HOME}/AAA`]: homeListing({ missing: true }),
      [`${HOME}/projects`]: { path: `${HOME}/projects`, parent: HOME, roots: homeListing().roots, entries: [] },
    });
    await renderDialog();
    await typeName('AAA', `${HOME}/AAA`);

    browse();

    expect(await screen.findByRole('button', { name: 'projects' })).toBeInTheDocument();
    expect(apiGet).toHaveBeenLastCalledWith('/api/project/folders', { params: { path: `${HOME}/AAA`, nearest: true } });
    expect(directoryInput()).toHaveValue(`${HOME}/AAA`);
    expect(nameInput()).toHaveValue('AAA');
    expect(toastMock.error).not.toHaveBeenCalled();

    // Nothing was picked, so the directory still follows the name.
    await typeName('AAB', `${HOME}/AAB`);

    // Picking a listed folder is what replaces it.
    fireEvent.click(screen.getByRole('button', { name: 'projects' }));
    await waitFor(() => expect(directoryInput()).toHaveValue(`${HOME}/projects`));
    expect(nameInput()).toHaveValue('AAB');
  });

  it('adopts the directory the server lists when it exists', async () => {
    serveFolders({
      '': homeListing(),
      '~/projects': { path: `${HOME}/projects`, parent: HOME, roots: homeListing().roots, entries: [] },
    });
    await renderDialog();
    fireEvent.change(directoryInput(), { target: { value: '~/projects' } });

    browse();

    await waitFor(() => expect(directoryInput()).toHaveValue(`${HOME}/projects`));
    expect(nameInput()).toHaveValue('projects');
    expect(screen.getByText('history.noSubfolders')).toBeInTheDocument();
  });

  it('says why the directory could not be read', async () => {
    serveFolders({
      '': homeListing(),
      '/srv/elsewhere': httpError(403, 'Directory is outside the allowed roots'),
    });
    await renderDialog();
    fireEvent.change(directoryInput(), { target: { value: '/srv/elsewhere' } });

    browse();

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(
      'history.folderBrowseFailed',
      'Directory is outside the allowed roots',
    ));
    expect(directoryInput()).toHaveValue('/srv/elsewhere');
    expect(screen.queryByRole('button', { name: 'history.parentFolder' })).not.toBeInTheDocument();
  });

  it('says why the folder could not be saved', async () => {
    serveFolders({ '': homeListing() });
    apiPost.mockRejectedValue(httpError(400, 'Project directory is outside the allowed roots'));
    const { onSaved } = await renderDialog();
    await typeName('AAA', `${HOME}/AAA`);

    fireEvent.click(screen.getByRole('button', { name: 'history.createFolder' }));

    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(
      'history.folderSaveFailed',
      'Project directory is outside the allowed roots',
    ));
    expect(apiPost).toHaveBeenCalledWith('/api/project', { name: 'AAA', worktree: `${HOME}/AAA` });
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('proposes a Windows directory with backslashes only', async () => {
    const winHome = 'C:\\ProgramData\\FlocksPro\\home';
    serveFolders({ '': homeListing({ path: winHome, roots: [{ name: 'home', path: winHome }], entries: [] }) });
    await renderDialog();

    await typeName('AAA', 'C:\\ProgramData\\FlocksPro\\home\\AAA');
  });
});

describe('joinFolderPath', () => {
  it.each([
    ['/home/u', 'AAA', '/home/u/AAA'],
    ['/home/u/', 'AAA', '/home/u/AAA'],
    ['/', 'AAA', '/AAA'],
    ['C:\\Users\\me', 'AAA', 'C:\\Users\\me\\AAA'],
    ['C:\\', 'AAA', 'C:\\AAA'],
    ['\\\\nas\\share\\', 'AAA', '\\\\nas\\share\\AAA'],
  ])('joins %s and %s with the separator the root uses', (root, name, expected) => {
    expect(joinFolderPath(root, name)).toBe(expected);
  });
});

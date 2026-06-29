import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import type {
  GenerationSettings,
  GenerationSettingsUpdate,
} from '@/lib/api/types';

const GENERATION_SETTINGS_KEY = ['settings', 'generation'] as const;

export function useGenerationSettings() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: GENERATION_SETTINGS_KEY,
    queryFn: () => apiClient.getGenerationSettings(),
    staleTime: Infinity,
  });

  const mutation = useMutation({
    mutationFn: (patch: GenerationSettingsUpdate) =>
      apiClient.updateGenerationSettings(patch),
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: GENERATION_SETTINGS_KEY });
      const previous = queryClient.getQueryData<GenerationSettings>(GENERATION_SETTINGS_KEY);
      if (previous) {
        queryClient.setQueryData<GenerationSettings>(GENERATION_SETTINGS_KEY, {
          ...previous,
          ...patch,
        });
      }
      return { previous };
    },
    onError: (_err, _patch, ctx) => {
      if (ctx?.previous) {
        queryClient.setQueryData(GENERATION_SETTINGS_KEY, ctx.previous);
      }
    },
    onSettled: (data) => {
      if (data) queryClient.setQueryData(GENERATION_SETTINGS_KEY, data);
    },
  });

  return {
    settings: query.data,
    isLoading: query.isLoading,
    update: mutation.mutate,
  };
}

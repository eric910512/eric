/**
 * Demo accounts for VITE_USE_MOCK=true. Same emails / password as the backend seed
 * (`flask seed dev`), plus patient02 for the high-risk demo. Sign-in and password changes
 * go through `@/mock/patients` (accounts created in mock mode sign in the same way). Synthetic data only.
 */
import { DEMO_PASSWORD } from '@/config/demo'

export const MOCK_PASSWORD = DEMO_PASSWORD

export const mockUsers = {
  'patient01@demo.local': {
    id: 'mock-user-patient01',
    display_name: '測試病人 甲',
    email: 'patient01@demo.local',
    role: 'patient',
    patient_id: '72ba2de4-f19d-4daf-96f9-03a5e83c07d0',
  },
  'patient02@demo.local': {
    id: 'mock-user-patient02',
    display_name: '測試病人 乙',
    email: 'patient02@demo.local',
    role: 'patient',
    patient_id: 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e',
  },
  'nurse01@demo.local': {
    id: 'mock-user-nurse01',
    display_name: '測試護理師 林',
    email: 'nurse01@demo.local',
    role: 'nurse',
    patient_id: null,
  },
  'admin01@demo.local': {
    id: 'mock-user-admin01',
    display_name: '測試管理者',
    email: 'admin01@demo.local',
    role: 'admin',
    patient_id: null,
  },
}

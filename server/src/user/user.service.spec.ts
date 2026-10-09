import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { HttpStatus } from '@nestjs/common';
import { verify } from 'jsonwebtoken';
import { UserService } from './user.service';
import { UserEntity } from './user.entity';

const buildUser = async (email: string, password: string) => {
  const user = new UserEntity();
  user.id = 'u-1';
  user.email = email;
  user.password = password;
  user.createdOn = new Date('2026-01-01');
  await user.hashPassword();
  return user;
};

describe('UserService', () => {
  let service: UserService;
  let repo: { findOne: jest.Mock; create: jest.Mock; save: jest.Mock };

  beforeAll(() => {
    process.env.SECRET = 'secret-de-test';
  });

  beforeEach(async () => {
    repo = { findOne: jest.fn(), create: jest.fn(), save: jest.fn() };
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        UserService,
        { provide: getRepositoryToken(UserEntity), useValue: repo },
      ],
    }).compile();
    service = module.get<UserService>(UserService);
  });

  it('le mot de passe est stocke hache, jamais en clair', async () => {
    const user = await buildUser('karim@test.fr', 'motdepasse123');
    expect(user.password).not.toBe('motdepasse123');
    expect(user.password).toMatch(/^\$2[aby]\$/);
    await expect(user.comparePassword('motdepasse123')).resolves.toBe(true);
  });

  it('connexion : renvoie un jeton valide et jamais le mot de passe', async () => {
    repo.findOne.mockResolvedValue(await buildUser('karim@test.fr', 'motdepasse123'));
    const result = await service.login({ email: 'karim@test.fr', password: 'motdepasse123' });
    expect(result).not.toHaveProperty('password');
    expect(verify(result.token, 'secret-de-test')).toMatchObject({ email: 'karim@test.fr' });
  });

  it('connexion : refuse un mauvais mot de passe', async () => {
    repo.findOne.mockResolvedValue(await buildUser('karim@test.fr', 'motdepasse123'));
    await expect(
      service.login({ email: 'karim@test.fr', password: 'mauvais' }),
    ).rejects.toMatchObject({ status: HttpStatus.UNAUTHORIZED });
  });

  it('connexion : meme message si l utilisateur n existe pas (pas d enumeration)', async () => {
    repo.findOne.mockResolvedValue(undefined);
    await expect(
      service.login({ email: 'inconnu@test.fr', password: 'x' }),
    ).rejects.toMatchObject({ message: 'Invalid email or password' });
  });

  it('inscription : refuse un e-mail deja utilise', async () => {
    repo.findOne.mockResolvedValue(await buildUser('karim@test.fr', 'motdepasse123'));
    await expect(
      service.register({ email: 'karim@test.fr', password: 'autre123' }),
    ).rejects.toMatchObject({ status: HttpStatus.BAD_REQUEST });
    expect(repo.save).not.toHaveBeenCalled();
  });

  it('inscription : enregistre et renvoie un jeton sans mot de passe', async () => {
    repo.findOne.mockResolvedValue(undefined);
    const created = await buildUser('nouveau@test.fr', 'motdepasse123');
    repo.create.mockReturnValue(created);
    const result = await service.register({ email: 'nouveau@test.fr', password: 'motdepasse123' });
    expect(repo.save).toHaveBeenCalledWith(created);
    expect(result).not.toHaveProperty('password');
    expect(result.token).toBeDefined();
  });
});

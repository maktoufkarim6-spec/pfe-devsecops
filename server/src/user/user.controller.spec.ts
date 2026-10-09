import { Test, TestingModule } from '@nestjs/testing';
import { UserController } from './user.controller';
import { UserService } from './user.service';

describe('UserController', () => {
  let controller: UserController;
  const service = { login: jest.fn(), register: jest.fn(), getProfile: jest.fn() };

  beforeEach(async () => {
    jest.resetAllMocks();
    const module: TestingModule = await Test.createTestingModule({
      controllers: [UserController],
      providers: [{ provide: UserService, useValue: service }],
    }).compile();
    controller = module.get<UserController>(UserController);
  });

  it('transmet les identifiants de connexion et d inscription au service', () => {
    const data = { email: 'karim@test.fr', password: 'motdepasse123' };
    controller.login(data);
    controller.register(data);
    expect(service.login).toHaveBeenCalledWith(data);
    expect(service.register).toHaveBeenCalledWith(data);
  });

  it('le profil est celui de l utilisateur du jeton', () => {
    controller.getProfile({ user: { id: '1', email: 'karim@test.fr' } });
    expect(service.getProfile).toHaveBeenCalledWith('karim@test.fr');
  });
});
